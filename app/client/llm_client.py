"""LLM 판정 클라이언트 (GMS 게이트웨이, 벤더 폴백 체인).

호출 방식은 벤더마다 다르고 그 차이는 `vendors.py`가 흡수한다. 이 파일이 하는 일은 셋이다 —
**어느 벤더로 부를지 고르고**, HTTP 실패를 분류하고, 선택 결과를 `JudgeResult`로 옮긴다.
client는 DB를 모른다(architecture.md §4).

**시도 예산은 체인 길이에 곱해지지 않는다.** `RetryPolicy.attempts`가 판정 호출 1건의
**총 HTTP 시도 횟수**이고, n번째 시도가 체인의 n번째 벤더를 쓴다(체인이 짧으면 마지막
벤더를 반복). 이유는 §3.2의 상한이다 — "두 호출의 타임아웃 합 + 재시도 시간 <
PROCESSING 만료 600s". 벤더마다 3회씩 재시도하면 최악이 3벤더 × 3시도 × 90s = 810s로
만료를 넘고, 그러면 재스캔이 아직 살아 있는 판정을 중복 실행해 비용이 배가 된다.
같은 벤더에 backoff를 걸고 다시 던지는 것보다 **막히지 않은 다른 경로로 즉시 넘어가는
편이 성공 확률도 높다** — 429는 프로바이더 경로별로 걸린다(vendors.py 실측).
체인을 벤더 하나로 두면 시도 배분이 그 벤더로 모여 폴백 이전과 정확히 같아진다.

`TransientError`(429·5xx·타임아웃·스키마 위반)만 다음 벤더로 넘어간다. `PermanentError`
(400·401·403)는 넘어가지 않는다 — 키·설정 문제는 다른 벤더에서도 같은 답이고, 재시도가
GMS 호출만 늘린다(S15P11A705-121 결함 3). 분류는 `classify_http_status` 하나를 쓴다
(failure-recovery.md §2.1·§2.2). 구조화 출력 위반은 재시도 대상이되 소진 후 영구 오류다(§2.2).
"""
from __future__ import annotations

import itertools
from typing import Sequence

import httpx

from app.client._calls import meter
from app.client._usage import record as record_usage
from app.client.retry import RetryPolicy, call_with_retry
from app.client.vendors import VendorCall, resolve_chain
from app.core.errors import (
    PermanentError,
    SchemaViolationError,
    TransientError,
    classify_http_status,
)
from app.core.redact import redact, redact_body
from app.schema.llm import JudgeResult, KeywordSelection

_TIMEOUT = 90.0

# 테스트 C-1에서 확정한 프롬프트. 부대시설/서비스 제외 규칙 포함
# (prompts/keyword_judgment.md).
SYSTEM = (
    "당신은 장소 기록 서비스의 Keyword 분류기입니다.\n"
    "사용자가 장소를 저장한 이유를 적은 짧은 글(Context)과 후보 Keyword 목록이 주어집니다.\n"
    "후보 목록에서 이 Context에 실제로 들어맞는 Keyword만 고르세요.\n"
    "규칙:\n"
    "- 반드시 후보 목록의 keyword_id 중에서만 고릅니다. 목록에 없는 것을 만들지 마세요.\n"
    "- 글에서 근거를 찾을 수 있는 것만 고릅니다. 그럴듯하다는 이유로 넣지 마세요.\n"
    "- 하나도 맞지 않으면 빈 목록을 반환합니다. 억지로 채우지 마세요.\n"
    "- 보통 0~3개입니다. 많이 고를수록 정확도가 떨어집니다.\n"
    "- description은 의미 범위, examples는 실제 말투 예시입니다. 둘 다 참고하세요.\n"
    "- 주차·화장실·직원 응대·가격 같은 부대시설이나 서비스 이야기는 장소의 Keyword가 아닙니다. "
    "장소에서 무엇을 했는지·누구와·어떤 분위기였는지만 고르세요.\n"
    "- confidence는 근거의 확실함을 0~1로 나타냅니다. 애매하면 낮게 줍니다.\n"
    "- unmatchedConcepts에는 후보로 표현하지 못한 핵심 개념을 짧게 적습니다(없으면 빈 배열)."
)


def build_user(context_text: str, candidates: list[dict]) -> str:
    lines = []
    for p in candidates:
        examples = " · ".join(p.get("examples", []))
        lines.append(
            f"- id={p['id']} | {p['display_name']} ({p['category']}) | "
            f"의미: {p['description']} | 예: {examples}"
        )
    return f"[Context]\n{context_text}\n\n[후보 Keyword]\n" + "\n".join(lines)


class LLMClient:
    def __init__(
        self,
        gms_base_url: str,
        api_key: str,
        chain: Sequence[tuple[str, str]],
        *,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """`chain`은 우선순위 순서의 `(vendor, model)` 목록이다(`Settings.judge_vendors`).

        지원하지 않는 벤더나 빈 체인이면 여기서 `ValueError`가 난다. 생성이 lifespan
        startup에 있으므로 잘못된 체인을 들고 뜨는 상태는 성립하지 않는다.
        """
        # GMS root에서 벤더별 네이티브 경로를 파생한다.
        self._root = gms_base_url.split("/gmsapi/")[0] + "/gmsapi"
        self._key = api_key
        self._chain = resolve_chain(chain)
        self._retry = retry or RetryPolicy()
        # transport는 테스트 이음새다(embedding_client와 같은 이유).
        self._transport = transport

    def _call_for(self, attempt: int) -> VendorCall:
        """n번째 시도가 쓸 벤더. 체인이 시도 횟수보다 짧으면 마지막 벤더를 반복한다.

        `min`을 쓰는 것이 계약이다 — 체인 길이와 `attempts`를 서로 맞추도록 강제하지
        않는다. 벤더 하나만 남긴 체인은 시도 전부가 그 벤더로 가고, 그것이 폴백 도입
        이전의 동작이다(설정만으로 되돌릴 수 있어야 한다는 요구).
        """
        return self._chain[min(attempt, len(self._chain) - 1)]

    async def judge(self, context_text: str, candidates: list[dict]) -> JudgeResult:
        user = build_user(context_text, candidates)
        # 시도 번호는 재시도 드라이버가 아니라 이 카운터가 센다. `call_with_retry`는
        # "같은 op를 다시 부른다"는 계약이므로, 벤더 전환을 op 안에 둬야 백오프·오류
        # 분류·소진 시 트레이스백 유지를 그 드라이버에서 그대로 물려받을 수 있다.
        attempts = itertools.count()
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, transport=self._transport
        ) as client:

            async def attempt() -> JudgeResult:
                return await self._judge_once(client, self._call_for(next(attempts)), user)

            try:
                return await call_with_retry(attempt, self._retry, stage="keyword")
            except SchemaViolationError as exc:
                # §2.2: 재시도 후에도 스키마 위반이면 영구 오류다. 승격을 여기서 끝내
                # service는 두 분류(Transient/Permanent)만 보게 한다.
                raise PermanentError(
                    f"llm schema violation after {self._retry.attempts} attempt(s): {exc}"
                ) from exc

    async def _judge_once(
        self, client: httpx.AsyncClient, call: VendorCall, user: str
    ) -> JudgeResult:
        """벤더 1곳에 1회 호출. 재시도·폴백 여부는 던지는 오류 타입이 결정한다(retry.py).

        **시도 하나가 계측 단위다.** 폴백은 시도마다 벤더를 바꾸므로(`_call_for`), 판정
        1건이 아니라 시도 1회를 세야 "어느 경로가 막혔나"가 집계에 남는다(`_calls.py`).
        """
        url, headers, body = call.adapter.request(
            self._root, self._key, call.model, SYSTEM, user
        )
        async with meter.call(
            "judge", model=call.model, vendor=call.adapter.vendor
        ) as rec:
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                # 응답이 없으므로 상태 코드 자리에 예외 타입 이름을 넣는다. 타임아웃과
                # 연결 실패는 둘 다 transient 지만 처방이 다르다 — 앞은 GMS 가 느린
                # 것이고 뒤는 경로가 아예 막힌 것이다. 메시지 본문에는 URL 이 섞여
                # 들어올 수 있어 그쪽이 아니라 타입 이름을 쓴다.
                rec.status = type(exc).__name__
                raise TransientError(
                    f"llm request failed ({call.label}): {redact(str(exc))}"
                ) from exc

            rec.status = resp.status_code
            if resp.status_code != 200:
                # 게이트웨이 오류를 일괄 일시 오류로 두지 않는다 — 400·401·403은 키·설정을
                # 고치기 전까지 같은 답이므로 다음 벤더로 넘겨도 같은 답이 온다.
                # 메시지에 벤더를 넣는다 — 폴백이 있으면 "어느 경로가 막혔나"가 원인 그 자체다.
                # 본문은 마스킹해서 싣는다(S15P11A705-205, `core/redact.py`) — 이 문자열이
                # `retry.py`·두 service·트레이스백까지 다섯 경로로 로그가 된다.
                raise classify_http_status(
                    resp.status_code,
                    f"llm error: {call.label} {resp.status_code} {redact_body(resp.text)}",
                )

            try:
                payload = resp.json()
            except ValueError as exc:
                raise SchemaViolationError(
                    f"llm response not json ({call.label}): {exc}"
                ) from exc
            # 어느 벤더·모델이 답했는지 남긴다. 폴백이 있으면 설정값(1순위)은 실제로 답한
            # 모델과 다를 수 있으므로, 사후 비용·품질 집계의 근거는 이 기록뿐이다.
            record_usage("judge", payload, vendor=call.adapter.vendor, model=call.model)
            # 봉투 해석까지 안에 둔다. 200 을 받고도 구조화 출력이 깨지면 그 호출은
            # 실패이고, 그것이 `_calls.SCHEMA` 로 갈리는 유일한 경로다.
            return self._parse(call.adapter.parse(payload), call.model)

    @staticmethod
    def _parse(data: dict, model: str) -> JudgeResult:
        """선택 객체 → `JudgeResult`. 벤더 무관한 변환이므로 한 벌만 둔다."""
        try:
            selected = [
                KeywordSelection(
                    keyword_id=int(s["keywordId"]),
                    confidence=(
                        float(s["confidence"])
                        if s.get("confidence") is not None
                        else None
                    ),
                )
                for s in data.get("selected", [])
                if "keywordId" in s
            ]
            unmatched = [str(x) for x in data.get("unmatchedConcepts", [])]
        except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
            # 구조화 출력 위반. 후보 절단(MAX_TOKENS)·안전 차단·타입 위반이 여기로 들어온다.
            raise SchemaViolationError(f"llm parse failed ({model}): {exc}") from exc
        return JudgeResult(selected=selected, unmatched_concepts=unmatched, model=model)

"""LLM 판정 클라이언트 (GMS 게이트웨이, Gemini generateContent).

테스트 C-2에서 확정한 호출 방식을 그대로 옮겼다(tools/keyword_eval/test_c_judge.py):
gemini-2.5-flash + responseSchema(네이티브 구조화 출력) + thinkingBudget=0.
function-calling은 2.5-flash에서 코드형 호출로 malformed 되므로 쓰지 않는다.

GMS는 도메인별 네이티브 인증을 통과시킨다 — Gemini는 x-goog-api-key.
client는 DB를 모른다. HTTP 실패는 분류된 오류로 service까지 올린다.

분류는 `classify_http_status` 하나를 쓴다(failure-recovery.md §2.1·§2.2). 이 파일이
**모든 non-200을 Transient로** 두어 인증 실패가 재스캔 주기(5분)마다 GMS 호출을 만들던 것이
S15P11A705-121의 결함 3이었다. 구조화 출력 위반은 재시도 대상이되 소진 후 영구 오류다(§2.2).
"""
from __future__ import annotations

import json
from functools import partial

import httpx

from app.client._usage import record as record_usage
from app.client.retry import RetryPolicy, call_with_retry
from app.core.errors import (
    PermanentError,
    SchemaViolationError,
    TransientError,
    classify_http_status,
)
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

# keywordId enum은 두지 않는다. 후보 밖 값은 매핑 단계에서 폐기한다(keyword-preset.md §4.3).
_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["selected"],
    "properties": {
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["keywordId", "confidence"],
                "properties": {
                    "keywordId": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "unmatchedConcepts": {"type": "array", "items": {"type": "string"}},
    },
}


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
        model: str,
        *,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # GMS root에서 Gemini 네이티브 경로를 파생한다.
        self._root = gms_base_url.split("/gmsapi/")[0] + "/gmsapi"
        self._key = api_key
        self._model = model
        self._retry = retry or RetryPolicy()
        # transport는 테스트 이음새다(embedding_client와 같은 이유).
        self._transport = transport

    async def judge(self, context_text: str, candidates: list[dict]) -> JudgeResult:
        user = build_user(context_text, candidates)
        url = (
            f"{self._root}/generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent"
        )
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _RESPONSE_SCHEMA,
                "maxOutputTokens": 2048,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, transport=self._transport
        ) as client:
            try:
                return await call_with_retry(
                    partial(self._judge_once, client, url, body),
                    self._retry,
                    stage="keyword",
                )
            except SchemaViolationError as exc:
                # §2.2: 재시도 후에도 스키마 위반이면 영구 오류다. 승격을 여기서 끝내
                # service는 두 분류(Transient/Permanent)만 보게 한다.
                raise PermanentError(
                    f"llm schema violation after {self._retry.attempts} attempt(s): {exc}"
                ) from exc

    async def _judge_once(
        self, client: httpx.AsyncClient, url: str, body: dict
    ) -> JudgeResult:
        """1회 호출. 재시도 여부는 던지는 오류 타입이 결정한다(retry.py)."""
        try:
            resp = await client.post(
                url,
                headers={
                    "x-goog-api-key": self._key,
                    "content-type": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError as exc:
            raise TransientError(f"llm request failed: {exc}") from exc

        if resp.status_code != 200:
            # 게이트웨이 오류를 일괄 일시 오류로 두지 않는다 — 400·401·403은 키·설정을
            # 고치기 전까지 같은 답이므로, 재스캔에 맡기면 5분마다 같은 호출을 만든다.
            raise classify_http_status(
                resp.status_code, f"llm error: {resp.status_code} {resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise SchemaViolationError(f"llm response not json: {exc}") from exc
        record_usage("judge", payload)
        return self._parse(payload)

    @staticmethod
    def _parse(payload: dict) -> JudgeResult:
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
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
            # 구조화 출력 위반. json.JSONDecodeError는 ValueError 하위다. 후보 절단
            # (MAX_TOKENS)·안전 차단·타입 위반이 모두 여기로 들어온다.
            raise SchemaViolationError(f"llm parse failed: {exc}") from exc
        return JudgeResult(selected=selected, unmatched_concepts=unmatched)

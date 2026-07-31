"""Embedding API 클라이언트 (GMS 게이트웨이, OpenAI 호환 /embeddings).

tools/keyword_eval/embed.py의 동기 클라이언트를 async로 포팅했다. 파일 캐시는
평가용이므로 제거하고, httpx.AsyncClient로 요청당 1회 호출한다.

client는 DB를 모른다(architecture.md §4). 차원 불일치는 영구 오류로 분류한다
(model-profile.md §5).

상태 코드 분류는 `classify_http_status` 하나에 맡긴다(failure-recovery.md §2.1·§2.2).
이 파일이 `status >= 500`만 Transient로 보고 나머지 non-200을 전부 Permanent로 두어
**429 한 번에 Context가 영구 실패**하던 것이 S15P11A705-121의 결함 2였다.
"""
from __future__ import annotations

from functools import partial

import httpx

from app.client._calls import meter
from app.client._usage import record as record_usage
from app.client.retry import RetryPolicy, call_with_retry
from app.core.errors import PermanentError, TransientError, classify_http_status
from app.core.redact import redact, redact_body

_BATCH = 128
_TIMEOUT = 60.0


def preset_embed_text(preset: dict) -> str:
    """Preset 임베딩 입력 텍스트. Context 검색과 필드 구성을 맞춘다.

    tools/keyword_eval/embed.py와 동일한 구성:
        "{display_name}. {description} {examples를 공백으로 연결}"
    """
    examples = " ".join(preset.get("examples", []))
    return f"{preset['display_name']}. {preset['description']} {examples}".strip()


class EmbeddingClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        *,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._dimension = dimension
        self._retry = retry or RetryPolicy()
        # transport는 테스트 이음새다(httpx.MockTransport). 운영에서는 None이며 httpx가
        # 기본 전송을 만든다. 상태 코드→오류 타입 매핑은 이 이음새 없이는 검증할 수 없다.
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 목록을 임베딩. 순서를 보존한다."""
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, transport=self._transport
        ) as client:
            for start in range(0, len(texts), _BATCH):
                batch = texts[start : start + _BATCH]
                # 재시도는 배치 1건 = API 호출 1회 단위다(§3.1 "단일 API 호출 안에서만").
                # 성공한 앞 배치를 다시 보내지 않는다.
                vectors.extend(
                    await call_with_retry(
                        partial(self._embed_batch, client, batch),
                        self._retry,
                        stage="embedding",
                    )
                )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]

    async def _embed_batch(
        self, client: httpx.AsyncClient, batch: list[str]
    ) -> list[list[float]]:
        # 계측 단위는 배치 1건 = API 호출 1회다(재시도 단위와 같다). 벤더를 붙이지
        # 않는다 — 임베딩은 폴백 대상이 아니라 경로가 하나뿐이다(_usage.py 와 동일).
        async with meter.call("embedding", model=self._model) as rec:
            try:
                resp = await client.post(
                    f"{self._base}/embeddings",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={"model": self._model, "input": batch},
                )
            except httpx.HTTPError as exc:
                # 타임아웃·연결 실패·DNS 실패 (§2.1). httpx.TimeoutException·ConnectError가
                # 모두 HTTPError 하위이므로 한 곳에서 일시 오류로 받는다.
                rec.status = type(exc).__name__
                # 메시지에 요청 URL이 섞여 들어올 수 있다(DNS 실패·프록시 오류). `rec.status`를
                # 타입 이름으로 두는 것만으로는 부족하다 — 이 메시지가 그대로 로그가 된다.
                raise TransientError(
                    f"embedding request failed: {redact(str(exc))}"
                ) from exc

            rec.status = resp.status_code
            if resp.status_code != 200:
                # 본문 200자는 "400이 왜 났는지"의 유일한 단서라 지우지 않는다. 대신
                # 자격 증명·endpoint 만 마스킹한다(S15P11A705-205, `core/redact.py`).
                raise classify_http_status(
                    resp.status_code,
                    f"embedding error: {resp.status_code} {redact_body(resp.text)}",
                )

            try:
                payload = resp.json()
                record_usage("embedding", payload)
                data = sorted(payload["data"], key=lambda d: d["index"])
                vectors = [item["embedding"] for item in data]
            except (ValueError, KeyError, TypeError) as exc:
                # 응답 형식 위반. 재시도해도 같은 형식이 오므로 영구 오류다(§2.2 입력 형식 오류).
                # 분류되지 않은 예외로 새면 BackgroundTasks에 트레이스백만 남고 단계는
                # PROCESSING에 머문다 — 영구 오류가 일시 오류처럼 행동하게 된다.
                raise PermanentError(f"embedding response malformed: {exc}") from exc

            # 차원 검사도 계측 안에 둔다. 200 을 받고도 쓸 수 없는 응답이면 그 호출은
            # 실패이고, `status=200 outcome=permanent` 조합이 그것을 게이트웨이 실패와
            # 구분해 준다 — Profile 이 어긋난 배포에서는 이 칸만 올라간다.
            for vec in vectors:
                if len(vec) != self._dimension:
                    raise PermanentError(
                        f"embedding dimension {len(vec)} != expected {self._dimension}"
                    )
            return vectors

"""client 호출 단위 방어 — 상태 코드 → 오류 분류, 짧은 재시도, 백오프 주입.

**이 파일은 integration-tests.md §5의 4계층에 속하지 않는다.** §4.2("HTTP 레벨 목이 아니라
인터페이스 레벨 Fake")는 *파이프라인 시나리오가 client를 무엇으로 대체하는가*의 규칙이고,
여기서 검증하는 것은 그 대체물이 아니라 **실제 client 자신의 HTTP 계층**이다. 두 계층 모두
필요하다 — 파이프라인 Fake는 `_embed_batch`가 429를 어떻게 분류하는지 볼 수 없고, 그 공백이
429를 영구 오류로, LLM 401을 일시 오류로 둔 채 남긴 직접 원인이었다(S15P11A705-121 결함 5).

DB·Docker·네트워크가 필요 없다. `httpx.MockTransport`로 프로바이더 응답을 직접 만들고,
`RetryPolicy.sleep`에 기록용 코루틴을 주입해 **실제로 잠들지 않는다**. jitter도 항등으로
주입해 백오프 수열을 값으로 단언한다.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.client.embedding_client import _BATCH, EmbeddingClient
from app.client.llm_client import LLMClient
from app.client.retry import RetryPolicy, call_with_retry
from app.core.errors import PermanentError, SchemaViolationError, TransientError

_DIM = 4
_BASE = "https://gms.example/gmsapi/api.openai.com/v1"
_CANDS = [
    {
        "id": 101,
        "display_name": "친구와",
        "category": "COMPANION",
        "description": "의미 범위",
        "examples": ["예시"],
    }
]


class _SleepRecorder:
    """주입된 sleep. 대기 시간을 순서대로 모으고 실제로는 잠들지 않는다."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _policy(sleep: _SleepRecorder, **kw) -> RetryPolicy:
    # jitter=항등 → 백오프 수열이 결정론적이 되어 값으로 단언할 수 있다.
    return RetryPolicy(sleep=sleep, jitter=lambda d: d, **kw)


def _transport(handler):
    """handler(request, call_no) → Response. 요청을 순서대로 기록한다."""
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    return httpx.MockTransport(wrapped), seen


def _emb(handler, sleep: _SleepRecorder, **kw):
    transport, seen = _transport(handler)
    client = EmbeddingClient(
        base_url=_BASE,
        api_key="k",
        model="text-embedding-3-small",
        dimension=_DIM,
        retry=_policy(sleep, **kw),
        transport=transport,
    )
    return client, seen


def _llm(handler, sleep: _SleepRecorder, **kw):
    # 체인을 벤더 하나로 둔다 — 이 파일이 고정하는 것은 **단일 벤더에서의** 상태 코드
    # 분류와 재시도이며, 그것이 폴백 도입 이전과 같아야 한다는 것이 회귀 기준이다.
    # 폴백 전환 자체는 test_llm_vendors.py가 본다.
    transport, seen = _transport(handler)
    client = LLMClient(
        gms_base_url=_BASE,
        api_key="k",
        chain=(("gemini", "gemini-2.5-flash"),),
        retry=_policy(sleep, **kw),
        transport=transport,
    )
    return client, seen


def _emb_ok(request: httpx.Request, _n: int) -> httpx.Response:
    inputs = json.loads(request.content)["input"]
    return httpx.Response(
        200,
        json={"data": [{"index": i, "embedding": [0.1] * _DIM} for i in range(len(inputs))]},
    )


def _llm_ok(request: httpx.Request, _n: int) -> httpx.Response:
    text = json.dumps({"selected": [{"keywordId": 101, "confidence": 0.9}],
                       "unmatchedConcepts": ["개념"]})
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def _always(code: int):
    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(code, text="provider says no")

    return handler


# ── 재시도 드라이버 (§3.1) ──────────────────────────────
async def test_transient_is_retried_up_to_two_times():
    sleep = _SleepRecorder()
    calls = {"n": 0}

    async def op():
        calls["n"] += 1
        raise TransientError("shaky")

    with pytest.raises(TransientError):
        await call_with_retry(op, _policy(sleep), stage="t")
    assert calls["n"] == 3                      # 최초 1회 + 재시도 2회 (총 3회)
    assert sleep.delays == [0.5, 1.0]           # 재시도 사이에만 대기


async def test_permanent_is_never_retried():
    sleep = _SleepRecorder()
    calls = {"n": 0}

    async def op():
        calls["n"] += 1
        raise PermanentError("auth")

    with pytest.raises(PermanentError):
        await call_with_retry(op, _policy(sleep), stage="t")
    assert calls["n"] == 1 and sleep.delays == []


async def test_success_after_transient_stops_retrying():
    sleep = _SleepRecorder()
    calls = {"n": 0}

    async def op():
        calls["n"] += 1
        if calls["n"] < 2:
            raise TransientError("shaky")
        return "ok"

    assert await call_with_retry(op, _policy(sleep), stage="t") == "ok"
    assert calls["n"] == 2 and sleep.delays == [0.5]


async def test_attempts_one_disables_retry():
    sleep = _SleepRecorder()
    calls = {"n": 0}

    async def op():
        calls["n"] += 1
        raise TransientError("shaky")

    with pytest.raises(TransientError):
        await call_with_retry(op, _policy(sleep, attempts=1), stage="t")
    assert calls["n"] == 1 and sleep.delays == []


# ── Embedding: 분류 (§2.1·§2.2) ─────────────────────────
async def test_embedding_429_is_transient_and_retried():
    # 결함 2: 429가 Permanent로 분류돼 rate limit 한 번에 Context가 영구 실패했다.
    sleep = _SleepRecorder()
    client, seen = _emb(_always(429), sleep)
    with pytest.raises(TransientError):
        await client.embed_one("t")
    assert len(seen) == 3
    assert sleep.delays == [0.5, 1.0]


@pytest.mark.parametrize("code", [500, 502, 503])
async def test_embedding_5xx_is_transient_and_retried(code):
    sleep = _SleepRecorder()
    client, seen = _emb(_always(code), sleep)
    with pytest.raises(TransientError):
        await client.embed_one("t")
    assert len(seen) == 3


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
async def test_embedding_4xx_is_permanent_and_not_retried(code):
    sleep = _SleepRecorder()
    client, seen = _emb(_always(code), sleep)
    with pytest.raises(PermanentError):
        await client.embed_one("t")
    assert len(seen) == 1 and sleep.delays == []


async def test_embedding_timeout_is_transient_and_retried():
    sleep = _SleepRecorder()

    def handler(request: httpx.Request, _n: int) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    client, seen = _emb(handler, sleep)
    with pytest.raises(TransientError):
        await client.embed_one("t")
    assert len(seen) == 3


async def test_embedding_connect_error_is_transient_and_retried():
    sleep = _SleepRecorder()

    def handler(request: httpx.Request, _n: int) -> httpx.Response:
        raise httpx.ConnectError("dns failure", request=request)

    client, seen = _emb(handler, sleep)
    with pytest.raises(TransientError):
        await client.embed_one("t")
    assert len(seen) == 3


async def test_embedding_recovers_within_retry_budget():
    sleep = _SleepRecorder()

    def handler(request: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(503, text="x") if n < 3 else _emb_ok(request, n)

    client, seen = _emb(handler, sleep)
    vec = await client.embed_one("t")
    assert len(vec) == _DIM and len(seen) == 3   # 3번째 시도에서 성공


async def test_embedding_dimension_mismatch_is_permanent_and_not_retried():
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1] * (_DIM + 1)}]}
        )

    client, seen = _emb(handler, sleep)
    with pytest.raises(PermanentError):
        await client.embed_one("t")
    assert len(seen) == 1                       # 차원 불일치는 설정 문제(model-profile §5)


async def test_embedding_malformed_response_is_permanent_and_not_retried():
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client, seen = _emb(handler, sleep)
    with pytest.raises(PermanentError):
        await client.embed_one("t")
    assert len(seen) == 1                       # 분류되지 않은 예외로 새지 않는다


async def test_embedding_retry_does_not_resend_successful_batch():
    # §3.1 "단일 API 호출 안에서만" — 재시도 단위는 배치 1건이다.
    sleep = _SleepRecorder()
    failed_once = {"done": False}

    def handler(request: httpx.Request, _n: int) -> httpx.Response:
        size = len(json.loads(request.content)["input"])
        if size < _BATCH and not failed_once["done"]:
            failed_once["done"] = True          # 두 번째(꼬리) 배치만 한 번 흔든다
            return httpx.Response(503, text="x")
        return _emb_ok(request, _n)

    client, seen = _emb(handler, sleep)
    vectors = await client.embed([f"t{i}" for i in range(_BATCH + 2)])
    assert len(vectors) == _BATCH + 2
    sizes = [len(json.loads(r.content)["input"]) for r in seen]
    assert sizes == [_BATCH, 2, 2]              # 성공한 첫 배치를 다시 보내지 않는다


# ── LLM: 분류 (§2.2) ────────────────────────────────────
@pytest.mark.parametrize("code", [400, 401, 403])
async def test_llm_4xx_is_permanent_and_not_retried(code):
    # 결함 3: 모든 non-200이 Transient여서 인증 실패가 재스캔 주기마다 GMS를 호출했다.
    sleep = _SleepRecorder()
    client, seen = _llm(_always(code), sleep)
    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)
    assert len(seen) == 1 and sleep.delays == []


async def test_llm_429_is_transient_and_retried():
    sleep = _SleepRecorder()
    client, seen = _llm(_always(429), sleep)
    with pytest.raises(TransientError):
        await client.judge("본문", _CANDS)
    assert len(seen) == 3 and sleep.delays == [0.5, 1.0]


async def test_llm_5xx_is_transient_and_retried():
    sleep = _SleepRecorder()
    client, seen = _llm(_always(503), sleep)
    with pytest.raises(TransientError):
        await client.judge("본문", _CANDS)
    assert len(seen) == 3


async def test_llm_timeout_is_transient_and_retried():
    sleep = _SleepRecorder()

    def handler(request: httpx.Request, _n: int) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    client, seen = _llm(handler, sleep)
    with pytest.raises(TransientError):
        await client.judge("본문", _CANDS)
    assert len(seen) == 3


async def test_llm_success_is_a_single_call():
    sleep = _SleepRecorder()
    client, seen = _llm(_llm_ok, sleep)
    result = await client.judge("본문", _CANDS)
    assert [s.keyword_id for s in result.selected] == [101]
    assert result.unmatched_concepts == ["개념"]
    assert len(seen) == 1 and sleep.delays == []


# ── LLM: 구조화 출력 위반 (§2.2 "재시도 후에도") ──────────
async def test_llm_schema_violation_is_retried_then_permanent():
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        # responseSchema를 줬어도 절단·안전 차단이면 이 형태가 온다.
        return httpx.Response(200, json={"candidates": [{"content": {"parts": []}}]})

    client, seen = _llm(handler, sleep)
    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)
    assert len(seen) == 3                       # 재시도 대상(출력은 비결정론적)
    assert sleep.delays == [0.5, 1.0]


async def test_llm_schema_violation_recovers_within_retry():
    sleep = _SleepRecorder()

    def handler(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": []}}]})
        return _llm_ok(request, n)

    client, seen = _llm(handler, sleep)
    result = await client.judge("본문", _CANDS)
    assert [s.keyword_id for s in result.selected] == [101]
    assert len(seen) == 2                       # 두 번째 시도에서 유효한 출력


async def test_llm_non_json_text_is_schema_violation():
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "not json{"}]}}]}
        )

    client, seen = _llm(handler, sleep)
    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)
    assert len(seen) == 3


async def test_llm_non_json_body_is_schema_violation():
    # 게이트웨이가 200으로 HTML 오류 페이지를 돌려주는 경우. resp.json() 자체가 실패한다.
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway error</html>")

    client, seen = _llm(handler, sleep)
    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)
    assert len(seen) == 3


async def test_schema_violation_does_not_escape_as_transient():
    """service가 이 타입을 보면 PROCESSING 유지로 처리해 무한 재판정이 된다.

    `judge`는 소진 시 반드시 `PermanentError`로 승격한다 — 하위 타입인 채로 나가면
    `except TransientError`가 먼저 잡는다.
    """
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(200, json={"candidates": []})

    client, _ = _llm(handler, sleep)
    with pytest.raises(PermanentError) as caught:
        await client.judge("본문", _CANDS)
    assert not isinstance(caught.value, SchemaViolationError)

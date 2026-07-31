"""GMS 호출 로그 — 실패를 실제로 만들어 그 행이 나오는 것을 본다.

**"로그 코드를 넣었다"는 검증이 아니다.** 그래서 이 파일은 계측 함수를 직접 부르는 대신
`httpx.MockTransport` 로 429·401·타임아웃·깨진 봉투를 만들어 두 클라이언트의 실제 호출
경로를 태우고, `caplog` 로 나온 행을 읽는다. 서버도 GMS 도 필요 없다.

세 가지를 고정한다.

1. **실패가 보인다** — 상태 코드와 결과 분류가 행에 있다. 이것이 없으면 시연 중
   느려졌을 때 우리 문제인지 게이트웨이 문제인지 가릴 수 없다(S15P11A705-197).
2. **성공은 조용하다** — DEBUG 다. dev 로그가 GMS 호출로 뒤덮이면 실패 행을 못 찾는다.
3. **실리지 않아야 할 것이 없다** — 키·URL·요청 본문(사용자 Context 원문)·응답 본문.
   `app/api/probe.py` 가 세운 값 노출 금지를 로그에도 적용한 것이며, 이 단언이 그 기준을
   코드로 고정하는 유일한 지점이다.
"""
from __future__ import annotations

import logging

import httpx
import pytest

from app.client import _calls
from app.client._calls import CallMeter
from app.client.embedding_client import EmbeddingClient
from app.client.llm_client import LLMClient
from app.client.retry import RetryPolicy
from app.core.errors import PermanentError, TransientError
from app.core.logging import configure_logging

LOGGER = "app.client.gms"

# 로그에 새면 안 되는 값들. 일부러 눈에 띄는 문자열을 쓴다 — 부분 노출도 잡기 위해서다.
GMS_BASE = "https://gms.example/gmsapi/api.openai.com/v1"
API_KEY = "sk-secret-gms-key-must-never-appear"
CONTEXT_TEXT = "성수동 골목 끝 카페에서 친구와 두 시간 — 사용자 원문이다"
ERROR_BODY = "quota exceeded for project secret-project-id"

CANDIDATES = [
    {
        "id": 1,
        "display_name": "카페",
        "category": "PLACE",
        "description": "커피를 마신 장소",
        "examples": ["카페 갔다"],
    }
]


async def _no_sleep(_delay: float) -> None:
    """백오프를 실제로 기다리지 않는다(retry.py 의 주입 이음새)."""


def _policy(attempts: int = 1) -> RetryPolicy:
    return RetryPolicy(attempts=attempts, sleep=_no_sleep, jitter=lambda d: d)


def _llm(handler, *, chain=(("gemini", "gemini-2.5-flash"),), attempts: int = 1) -> LLMClient:
    return LLMClient(
        gms_base_url=GMS_BASE,
        api_key=API_KEY,
        chain=list(chain),
        retry=_policy(attempts),
        transport=httpx.MockTransport(handler),
    )


def _embedder(handler, *, dimension: int = 2) -> EmbeddingClient:
    return EmbeddingClient(
        base_url=GMS_BASE,
        api_key=API_KEY,
        model="text-embedding-3-small",
        dimension=dimension,
        retry=_policy(),
        transport=httpx.MockTransport(handler),
    )


def _call_rows(caplog) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == LOGGER and r.getMessage().startswith("gms call")
    ]


def _gemini_ok(_request: httpx.Request) -> httpx.Response:
    body = '{"selected": [{"keywordId": 1, "confidence": 0.9}], "unmatchedConcepts": []}'
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": body}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        },
    )


# ── 실패가 보이는가 ────────────────────────────────────────
@pytest.mark.parametrize(
    ("status", "expected_outcome", "expected_exc"),
    [
        # 429 는 게이트웨이 쿼터다. 이 칸이 올라가면 기다리는 것이 답이다.
        (429, "transient", TransientError),
        # 5xx 는 제공자 장애. 같은 transient 지만 상태 코드로 갈린다.
        (503, "transient", TransientError),
        # 401 은 키·설정 문제라 재시도도 폴백도 소용없다 — 우리 문제다.
        (401, "permanent", PermanentError),
        (400, "permanent", PermanentError),
    ],
)
async def test_http_failure_is_logged_with_status_and_outcome(
    caplog, status, expected_outcome, expected_exc
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=ERROR_BODY)

    with pytest.raises(expected_exc):
        await _llm(handler).judge(CONTEXT_TEXT, CANDIDATES)

    (row,) = _call_rows(caplog)
    message = row.getMessage()
    assert row.levelno == logging.WARNING
    assert f"status={status}" in message
    assert f"outcome={expected_outcome}" in message
    assert "kind=judge" in message and "vendor=gemini" in message
    assert "ms=" in message


async def test_transport_failure_logs_exception_type_not_url(caplog):
    """응답이 없으면 상태 코드 자리에 예외 타입 이름이 온다.

    타임아웃과 연결 실패는 둘 다 transient 지만 처방이 다르다 — 앞은 GMS 가 느린 것이고
    뒤는 경로가 막힌 것이다. 예외 **메시지**를 쓰지 않는 이유는 거기에 URL 이 섞이기
    때문이다(httpx 는 요청 URL 을 메시지에 넣는다).
    """
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(TransientError):
        await _llm(handler).judge(CONTEXT_TEXT, CANDIDATES)

    (row,) = _call_rows(caplog)
    assert "status=ConnectTimeout" in row.getMessage()
    assert "outcome=transient" in row.getMessage()
    assert "gms.example" not in row.getMessage()


async def test_broken_envelope_is_logged_as_schema_at_status_200(caplog):
    """200 인데 구조화 출력이 깨진 경우. 게이트웨이는 멀쩡했다.

    `status=200 outcome=schema` 조합이 이것을 429 와 구분한다. 섞여 보이면 처방이
    정반대인 두 실패를 같은 것으로 읽게 된다.
    """
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": []})  # parts 가 없다

    # 재시도가 소진되면 스키마 위반은 영구 오류로 승격된다(errors.py).
    with pytest.raises(PermanentError):
        await _llm(handler).judge(CONTEXT_TEXT, CANDIDATES)

    (row,) = _call_rows(caplog)
    assert "status=200" in row.getMessage()
    assert "outcome=schema" in row.getMessage()


async def test_embedding_failure_is_logged_without_vendor(caplog):
    """임베딩은 폴백 대상이 아니라 경로가 하나다 — 행에 벤더 자리가 비어 있다."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text=ERROR_BODY)

    with pytest.raises(TransientError):
        await _embedder(handler).embed_one("텍스트")

    (row,) = _call_rows(caplog)
    assert "kind=embedding" in row.getMessage()
    assert "vendor=-" in row.getMessage()
    assert "status=429 outcome=transient" in row.getMessage()


async def test_dimension_mismatch_is_permanent_at_status_200(caplog):
    """200 을 받고도 쓸 수 없는 응답. Profile 이 어긋난 배포에서 이 칸만 올라간다."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})

    with pytest.raises(PermanentError):
        await _embedder(handler, dimension=1536).embed_one("텍스트")

    (row,) = _call_rows(caplog)
    assert "status=200" in row.getMessage() and "outcome=permanent" in row.getMessage()


# ── 폴백이 어느 칸에서 막혔는지 ────────────────────────────
async def test_fallback_logs_one_row_per_vendor_attempt(caplog):
    """시도 하나가 계측 단위다 — 판정 1건이 아니라.

    폴백은 시도마다 벤더를 바꾸므로(`llm_client._call_for`), 이렇게 세야 "gemini 는
    막혔고 openai 는 통과했다"가 로그에 남는다. 판정 단위로 세면 그 사실이 사라진다.
    """
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "generativelanguage" in str(request.url):
            return httpx.Response(429, text=ERROR_BODY)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"selected": [], "unmatchedConcepts": []}'
                        }
                    }
                ]
            },
        )

    result = await _llm(
        handler,
        chain=(("gemini", "gemini-2.5-flash"), ("openai", "gpt-4o-mini")),
        attempts=2,
    ).judge(CONTEXT_TEXT, CANDIDATES)

    assert result.model == "gpt-4o-mini"
    first, second = _call_rows(caplog)
    assert "vendor=gemini" in first.getMessage() and "status=429" in first.getMessage()
    assert first.levelno == logging.WARNING
    assert "vendor=openai" in second.getMessage() and "outcome=ok" in second.getMessage()
    assert second.levelno == logging.DEBUG


# ── 성공은 조용한가 ────────────────────────────────────────
async def test_success_is_debug_not_info(caplog):
    """성공까지 INFO 로 남기면 dev 로그가 GMS 호출로 뒤덮여 실패 행을 못 찾는다."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    await _llm(_gemini_ok).judge(CONTEXT_TEXT, CANDIDATES)

    (row,) = _call_rows(caplog)
    assert row.levelno == logging.DEBUG
    assert "outcome=ok" in row.getMessage() and "status=200" in row.getMessage()


async def test_nothing_is_logged_at_info_for_a_single_success(caplog):
    """창(60s)이 차기 전에는 요약도 나오지 않는다 — INFO 가 한 줄도 없어야 한다."""
    caplog.set_level(logging.INFO, logger=LOGGER)

    await _llm(_gemini_ok).judge(CONTEXT_TEXT, CANDIDATES)

    assert [r for r in caplog.records if r.name == LOGGER] == []


# ── 실리지 않아야 할 것 ────────────────────────────────────
@pytest.mark.parametrize("failing", [True, False])
async def test_secrets_and_payloads_never_reach_the_log(caplog, failing):
    """키·URL·요청 본문·응답 본문 어느 것도 이 모듈의 행에 실리지 않는다.

    요청 본문에는 사용자가 쓴 Context 원문이 들어 있다. 성공 경로와 실패 경로를 모두
    태우는 이유는, 값 노출이 대개 오류 분기에서 생기지만 **분기마다** 확인해야 하기
    때문이다(`probe.py` 가 "어떤 분기에서도"라고 적은 것과 같은 이유).
    """
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=ERROR_BODY)

    if failing:
        with pytest.raises(PermanentError):
            await _llm(handler).judge(CONTEXT_TEXT, CANDIDATES)
    else:
        await _llm(_gemini_ok).judge(CONTEXT_TEXT, CANDIDATES)

    blob = "\n".join(r.getMessage() for r in caplog.records if r.name == LOGGER)
    assert blob  # 아무 행도 안 나왔으면 이 단언은 무의미하다
    for secret in (API_KEY, CONTEXT_TEXT, GMS_BASE, "gms.example", ERROR_BODY):
        assert secret not in blob
    # 벤더·모델은 반대로 **반드시** 있어야 한다. 공개 설정이고(P45), 어느 경로가
    # 막혔는지가 곧 원인이다.
    assert "gemini-2.5-flash" in blob and "vendor=gemini" in blob


# ── 창 요약 ────────────────────────────────────────────────
class _ManualClock:
    """호출 중에는 시간이 흐르지 않는다. 지연·창 경과를 테스트가 직접 정한다."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


async def _record(meter: CallMeter, clock: _ManualClock, *, outcome_exc=None, elapsed=0.0):
    try:
        async with meter.call("judge", model="m", vendor="gemini") as rec:
            clock.advance(elapsed)
            rec.status = 200 if outcome_exc is None else 429
            if outcome_exc is not None:
                raise outcome_exc
    except Exception:  # noqa: BLE001 — 여기서는 계측 결과만 본다
        pass


def _summaries(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER and r.getMessage().startswith("gms window")
    ]


async def test_window_summary_carries_the_failure_rate(caplog):
    """요약이 실패율의 분모를 센다. 개별 성공 행이 DEBUG 라 이 줄이 유일한 분모다."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    clock = _ManualClock()
    meter = CallMeter(window_sec=60.0, clock=clock)

    await _record(meter, clock, elapsed=1.0)
    await _record(meter, clock, outcome_exc=TransientError("429"), elapsed=3.0)
    assert _summaries(caplog) == []  # 창이 아직 안 찼다

    clock.advance(61)
    await _record(meter, clock, elapsed=0.5)

    (summary,) = _summaries(caplog)
    assert "calls=3" in summary
    assert "fail=1" in summary
    assert "fail_pct=33" in summary
    assert "ok=2" in summary and "transient=1" in summary
    assert "max_ms=3000" in summary
    assert "[judge:gemini ok=2 transient=1]" in summary


async def test_summary_is_pushed_by_the_next_call_not_a_timer(caplog):
    """호출이 끊기면 요약도 멈춘다 — 유휴 상태의 로그가 조용한 이유이자 그 대가다."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    clock = _ManualClock()
    meter = CallMeter(window_sec=60.0, clock=clock)

    await _record(meter, clock)
    clock.advance(3600)

    assert _summaries(caplog) == []  # 한 시간이 지나도 다음 호출 전에는 나오지 않는다


async def test_flush_emits_the_last_window(caplog):
    """종료 시 마지막 창을 잃지 않는다. 시연 끝나고 파드를 내리는 구간이 여기다."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    clock = _ManualClock()
    meter = CallMeter(window_sec=60.0, clock=clock)

    await _record(meter, clock, outcome_exc=TransientError("429"))
    meter.flush()

    (summary,) = _summaries(caplog)
    assert "calls=1" in summary and "fail_pct=100" in summary

    meter.flush()  # 두 번째 flush 는 낼 것이 없다
    assert len(_summaries(caplog)) == 1


# ── 계측이 본 작업을 죽이지 않는가 ─────────────────────────
async def test_logging_failure_is_swallowed(caplog, monkeypatch):
    """기록이 터져도 호출 결과는 그대로 나간다.

    `_usage.py` 와 같은 규칙이다. 이 방어가 없으면 로깅 설정 하나가 판정 경로 전체를
    죽인다 — 관측을 붙이려다 관측 대상을 없애는 것이 된다.
    """

    def explode(*_args, **_kwargs):
        raise RuntimeError("logging is broken")

    monkeypatch.setattr(_calls.log, "log", explode)

    result = await _llm(_gemini_ok).judge(CONTEXT_TEXT, CANDIDATES)

    assert [s.keyword_id for s in result.selected] == [1]


# ── httpx 가 URL 을 흘리지 않는가 ──────────────────────────
# 이 두 테스트는 위 caplog 단언이 **놓친 것**을 막는다. 로거 이름으로 걸러 읽으면
# `app.client.gms` 행만 보게 되어, 바로 옆에서 httpx 가 같은 호출의 URL 을 INFO 로
# 남기고 있어도 초록으로 통과한다. 실제로 그 상태였고 실행 출력을 눈으로 보고서야
# 드러났다(S15P11A705-197).
def test_configure_logging_quiets_httpx_request_lines():
    httpx_logger = logging.getLogger("httpx")
    original = httpx_logger.level
    try:
        httpx_logger.setLevel(logging.NOTSET)  # 설정 이전 상태로 되돌린다
        configure_logging()
        assert httpx_logger.level == logging.WARNING
    finally:
        httpx_logger.setLevel(original)


async def test_request_url_never_appears_in_any_log_record(caplog):
    """설정을 적용한 뒤 실제 호출을 태우고, **로거를 가리지 않고** 전부 읽는다."""
    httpx_logger = logging.getLogger("httpx")
    original = httpx_logger.level
    try:
        configure_logging()
        caplog.set_level(logging.DEBUG)  # root 를 낮춰도 httpx 로거는 WARNING 이다

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text=ERROR_BODY)

        with pytest.raises(TransientError):
            await _llm(handler).judge(CONTEXT_TEXT, CANDIDATES)

        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "gms call" in blob  # 우리 행은 나왔는가
        assert "gms.example" not in blob
        assert "generativelanguage" not in blob
        assert [r for r in caplog.records if r.name == "httpx"] == []
    finally:
        httpx_logger.setLevel(original)


def test_flush_failure_is_swallowed(monkeypatch):
    """종료 경로의 계측도 종료를 막지 않는다.

    `flush()` 는 lifespan 의 `finally` 에서 불린다(`app/main.py`). 여기서 예외가 새면
    DB 커넥션 정리가 건너뛰어진다 — 계측이 종료를 망가뜨리는 정확한 경로다.
    """
    meter = CallMeter()

    def explode(*_args, **_kwargs):
        raise RuntimeError("clock is broken")

    monkeypatch.setattr(meter, "_clock", explode)
    meter.flush()  # 예외가 나면 이 테스트가 실패한다


async def test_unclassified_exception_is_flagged(caplog):
    """두 분류 어디에도 안 걸린 예외는 ERROR 다.

    이 행이 보이면 오류 분류가 새고 있다는 뜻이고, 그 단계는 PROCESSING 에 머문다
    (failure-recovery.md §2). 조용히 ok 로 세면 그 사실이 묻힌다.
    """
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    meter = CallMeter()

    with pytest.raises(ZeroDivisionError):
        async with meter.call("judge", model="m", vendor="openai"):
            raise ZeroDivisionError("분류되지 않은 결함")

    (row,) = _call_rows(caplog)
    assert row.levelno == logging.ERROR
    assert "outcome=unclassified" in row.getMessage()

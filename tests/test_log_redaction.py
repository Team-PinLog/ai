"""게이트웨이 오류 본문이 **로그로 새는 것**을 막는다 (S15P11A705-205).

`test_client_retry.py`·`test_llm_vendors.py`와 같은 계층이다 — integration-tests.md §5의
4계층 밖이며 DB·Docker·네트워크가 필요 없다. 검증 대상이 파이프라인이 아니라 **client가
예외 메시지를 어떻게 만드는가**이고, 그 문자열은 인터페이스 레벨 Fake로는 볼 수 없다.

**막는 지점이 로그 호출부가 아니라 예외 메시지 생성부인 것이 이 파일의 전제다.** 같은
문자열이 다섯 군데에서 로그가 되고(`retry.py`·`embedding_service`×2·`keyword_service`×2)
분류 밖 예외는 트레이스백으로도 나간다. 호출부마다 가리면 다음에 늘어나는 여섯 번째를
놓친다. 그래서 단언도 "로그에 없다"와 "예외 메시지 자체에 없다"를 함께 건다.

픽스처의 자격 증명은 **실제 키처럼 보이지 않게** 지었다. 나중에 누가 진짜로 오인하면
그것 자체가 사고다.
"""
from __future__ import annotations

import ast
import json
import logging
import traceback
from pathlib import Path

import httpx
import pytest

from app.client.embedding_client import EmbeddingClient
from app.client.llm_client import LLMClient
from app.client.retry import RetryPolicy
from app.core.errors import PermanentError, TransientError
from app.core.redact import redact, redact_body

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

# 실제 키가 아니다. 형식만 각 규칙에 걸리게 두고 값은 한눈에 가짜여야 한다.
FAKE_OPENAI_KEY = "sk-NOT-A-REAL-KEY-USED-ONLY-IN-TESTS-0000"
FAKE_GOOGLE_KEY = "AIzaNOT-A-REAL-KEY-USED-ONLY-IN-TESTS"
FAKE_BEARER = "Bearer NOT-A-REAL-TOKEN-USED-ONLY-IN-TESTS"


def _policy(**kw) -> RetryPolicy:
    async def _no_sleep(_: float) -> None:
        return None

    return RetryPolicy(sleep=_no_sleep, jitter=lambda d: 0.0, **kw)


def _transport(status: int, body: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


# ── 1. 마스킹 규칙 자체 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        f'{{"message":"api key {FAKE_OPENAI_KEY} rejected"}}',
        f'{{"error":{{"authorization":"{FAKE_BEARER}"}}}}',
        f'{{"message":"invalid {FAKE_GOOGLE_KEY}"}}',
        f'{{"apiKey": "{FAKE_OPENAI_KEY}"}}',
        f'{{"x-api-key":"{FAKE_OPENAI_KEY}"}}',
    ],
)
def test_credentials_never_survive_redaction(raw: str) -> None:
    """자격 증명 패턴이 endpoint보다 우선이다 — 어느 형태로 실려 와도 값이 남지 않는다."""
    out = redact(raw)
    assert FAKE_OPENAI_KEY not in out
    assert FAKE_GOOGLE_KEY not in out
    assert "NOT-A-REAL-TOKEN-USED-ONLY-IN-TESTS" not in out
    # 값만 지우고 자리는 남긴다 — "키가 문제였다"는 사실은 진단 단서다.
    assert out != ""


def test_endpoint_is_redacted() -> None:
    """`probe.py`가 세운 값 노출 금지의 endpoint 항목을 예외 경로에도 적용한다."""
    out = redact("upstream https://gms.ssafy.io/gmsapi/api.openai.com/v1/embeddings failed")
    assert "gms.ssafy.io" not in out
    assert "://" not in out

    # URL 형태가 아니라 맨 호스트로 실려 와도 지운다(실측한 GMS 400 문구가 이 모양이다).
    bare = redact("Model not found in request for domain api.openai.com")
    assert "api.openai.com" not in bare
    assert "Model not found" in bare


@pytest.mark.parametrize(
    "diagnostic",
    [
        # 2026-07-31 실측한 실제 GMS 오류 본문. 진단 단서가 마스킹을 통과해야 한다 —
        # 무조건 지우면 "400이 왜 났는지"를 잃는다(티켓 확정 판단).
        '{"message":"[GMS 에러] Invalid or expired GMS key","statusCode":401}',
        '{"message":"[OpenAI 에러] Request failed with status code 400","statusCode":400,'
        '"error":{"error":{"message":"Invalid \'max_completion_tokens\': integer below '
        'minimum value. Expected a value >= 1, but got -1"}}}',
        '{"message":"[Anthropic 에러] Request failed with status code 400","statusCode":400,'
        '"error":{"error":{"type":"invalid_request_error",'
        '"message":"max_tokens: must be greater than or equal to 1"}}}',
        '{"message":"[Gemini 에러] Request failed with status code 400","statusCode":400,'
        '"error":{"error":{"code":400,"message":"Invalid JSON payload received."}}}',
    ],
)
def test_diagnostic_text_survives(diagnostic: str) -> None:
    assert redact(diagnostic) == diagnostic


def test_secret_split_across_truncation_does_not_survive() -> None:
    """**마스킹이 절단보다 먼저다.** 순서를 뒤집으면 200자 경계에 걸친 키의 앞부분이 남는다."""
    filler = "x" * 190
    out = redact_body(f'{{"m":"{filler}{FAKE_OPENAI_KEY}"}}', limit=200)
    assert "sk-NOT-A-REAL" not in out
    assert len(out) <= 200


def test_redact_body_truncates_to_limit() -> None:
    assert len(redact_body("y" * 5000, limit=200)) == 200


# ── 2. client 예외 메시지 — 로그·트레이스백의 원천 ─────────────────────────

_LEAKY_401 = json.dumps(
    {"message": f"api key {FAKE_OPENAI_KEY} rejected by https://gms.ssafy.io/gmsapi"}
)
_LEAKY_502 = json.dumps(
    {"message": f"upstream https://gms.ssafy.io/gmsapi refused {FAKE_BEARER}"}
)


def _assert_clean(text: str) -> None:
    assert FAKE_OPENAI_KEY not in text
    assert "sk-NOT-A-REAL" not in text
    assert "NOT-A-REAL-TOKEN" not in text
    assert "gms.ssafy.io" not in text


@pytest.mark.asyncio
async def test_embedding_permanent_error_message_is_clean() -> None:
    client = EmbeddingClient(
        _BASE, "unused", "m", _DIM, retry=_policy(), transport=_transport(401, _LEAKY_401)
    )
    with pytest.raises(PermanentError) as excinfo:
        await client.embed(["안녕"])
    _assert_clean(str(excinfo.value))
    # 분류 밖 예외가 uvicorn까지 올라가면 이 문자열이 트레이스백에 그대로 찍힌다.
    _assert_clean("".join(traceback.format_exception(excinfo.value)))
    # 진단은 남는다 — 상태 코드와 "무엇이 거절됐나"의 뼈대.
    assert "401" in str(excinfo.value)


@pytest.mark.asyncio
async def test_judge_permanent_error_message_is_clean() -> None:
    client = LLMClient(
        _BASE,
        "unused",
        [("openai", "test-model")],
        retry=_policy(),
        transport=_transport(401, _LEAKY_401),
    )
    with pytest.raises(PermanentError) as excinfo:
        await client.judge("본문", _CANDS)
    _assert_clean(str(excinfo.value))
    _assert_clean("".join(traceback.format_exception(excinfo.value)))


@pytest.mark.asyncio
async def test_retry_log_does_not_leak_response_body(caplog) -> None:
    """`retry.py`가 `str(exc)`를 WARNING으로 찍는다 — 재시도마다 한 줄씩 샌다."""
    client = EmbeddingClient(
        _BASE, "unused", "m", _DIM, retry=_policy(), transport=_transport(502, _LEAKY_502)
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(TransientError):
            await client.embed(["안녕"])

    assert any(r.name == "app.client.retry" for r in caplog.records), "재시도 로그가 없다"
    for record in caplog.records:
        _assert_clean(record.getMessage())


@pytest.mark.asyncio
async def test_all_gms_log_records_are_clean(caplog) -> None:
    """계측·재시도를 합쳐 이 호출이 남기는 **모든** 행을 훑는다."""
    client = LLMClient(
        _BASE,
        "unused",
        [("openai", "test-model")],
        retry=_policy(attempts=2),
        transport=_transport(502, _LEAKY_502),
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(TransientError):
            await client.judge("본문", _CANDS)

    assert caplog.records
    for record in caplog.records:
        _assert_clean(record.getMessage())


@pytest.mark.asyncio
async def test_transport_failure_message_is_clean() -> None:
    """응답이 없는 실패도 원천이다 — httpx 예외 메시지에는 URL이 섞여 들어올 수 있다."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "connection to https://gms.ssafy.io/gmsapi refused", request=request
        )

    client = EmbeddingClient(
        _BASE, "unused", "m", _DIM, retry=_policy(attempts=1),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(TransientError) as excinfo:
        await client.embed(["안녕"])
    _assert_clean(str(excinfo.value))


# ── 3. 구조 방어 — 원천이 하나로 유지되는가 ────────────────────────────────

_APP = Path(__file__).resolve().parent.parent / "app"
_RESPONSE_NAMES = {"resp", "response", "r"}


def _body_reads(tree: ast.AST) -> set[ast.AST]:
    """`resp.text` 꼴의 응답 본문 접근 노드 전부."""
    return {
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "text"
        and isinstance(node.value, ast.Name)
        and node.value.id in _RESPONSE_NAMES
    }


def _redacted_reads(tree: ast.AST) -> set[ast.AST]:
    """`redact_body(...)` 인자 안에 들어 있는 본문 접근 노드."""
    covered: set[ast.AST] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("redact_body", "redact")
        ):
            for arg in node.args:
                covered |= _body_reads(arg)
    return covered


def test_no_unredacted_response_body_in_app() -> None:
    """다음에 늘어나는 여섯 번째 호출부를 막는 것은 이 단언이다.

    호출부마다 마스킹을 거는 설계였다면 이 검사를 쓸 수 없다 — 어디가 호출부인지
    세는 일이 사람 몫으로 남는다.

    **AST로 본다.** 텍스트로 훑으면 `gms_roundtrip.py`의 docstring이 `resp.text[:200]`을
    설명하는 것까지 위반으로 잡힌다 — 산문을 코드로 오인하는 검사는 오래 못 간다.
    """
    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _body_reads(tree) - _redacted_reads(tree):
            offenders.append(f"{path.relative_to(_APP.parent)}:{node.lineno}")
    assert not offenders, (
        "응답 본문을 마스킹 없이 쓰는 곳이 있다 — `redact_body(resp.text)`를 통과시켜라: "
        + ", ".join(sorted(offenders))
    )

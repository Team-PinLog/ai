"""판정 LLM 벤더 어댑터와 폴백 체인 — 세 형식의 요청·응답, 폴백 발동 조건.

**이 파일은 integration-tests.md §5의 4계층에 속하지 않는다.** `test_client_retry.py`와
같은 계층(client 자신의 HTTP 계층)이며 DB·Docker·네트워크가 필요 없다. 인터페이스 레벨
Fake로는 "429를 받고 다른 벤더로 넘어갔는가"를 볼 수 없다 — 그 전환은 HTTP 응답에서
시작하기 때문이다.

`httpx.MockTransport`로 세 프로바이더의 응답 봉투를 직접 만들고, `RetryPolicy.sleep`에
기록용 코루틴을 주입해 **실제로 잠들지 않는다**.

폴백 순서·모델은 여기서 리터럴로 쓰지 않는다(`_CHAIN`) — 순서를 바꾸는 것은 설정 변경이며
테스트가 특정 순서에 묶이면 그 변경이 테스트 실패로 나타난다. 대신 **설정 기본값이 무엇인지**는
`test_unit.py`가 한 곳에서 단언한다.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.client._usage import _JUDGE_TOKENS
from app.client.llm_client import LLMClient
from app.client.retry import RetryPolicy
from app.client.vendors import ADAPTERS, resolve_chain
from app.core.errors import PermanentError, TransientError

_BASE = "https://gms.example/gmsapi/api.openai.com/v1"
_ROOT = "https://gms.example/gmsapi"
_KEY = "gms-key"
_CANDS = [
    {
        "id": 101,
        "display_name": "친구와",
        "category": "COMPANION",
        "description": "의미 범위",
        "examples": ["예시"],
    }
]

# 폴백 순서를 세 벤더로 덮는 체인. 모델명은 이 파일 안에서만 의미가 있다.
_CHAIN = (
    ("openai", "primary-model"),
    ("gemini", "second-model"),
    ("anthropic", "third-model"),
)

_SELECTION = {"selected": [{"keywordId": 101, "confidence": 0.9}], "unmatchedConcepts": ["개념"]}


class _SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _client(handler, sleep: _SleepRecorder, chain=_CHAIN, **kw):
    """handler(request, call_no) → Response. 요청을 순서대로 기록한다."""
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    client = LLMClient(
        gms_base_url=_BASE,
        api_key=_KEY,
        chain=chain,
        retry=RetryPolicy(sleep=sleep, jitter=lambda d: d, **kw),
        transport=httpx.MockTransport(wrapped),
    )
    return client, seen


# ── 벤더별 응답 봉투 (형식이 셋 다 다르다) ────────────────
def _openai_response(selection: dict | None = None) -> httpx.Response:
    body = json.dumps(selection if selection is not None else _SELECTION)
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": body}}],
            "usage": {"prompt_tokens": 821, "completion_tokens": 15, "total_tokens": 836},
        },
    )


def _gemini_response(selection: dict | None = None) -> httpx.Response:
    body = json.dumps(selection if selection is not None else _SELECTION)
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": body}]}}],
            "usageMetadata": {
                "promptTokenCount": 732,
                "candidatesTokenCount": 21,
                "thoughtsTokenCount": 0,
                "totalTokenCount": 753,
            },
        },
    )


def _anthropic_response(selection: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "content": [
                {"type": "text", "text": "고른 결과를 보고합니다"},
                {
                    "type": "tool_use",
                    "name": "select_keywords",
                    "input": selection if selection is not None else _SELECTION,
                },
            ],
            "usage": {"input_tokens": 1973, "output_tokens": 57},
        },
    )


_RESPONSES = {
    "openai": _openai_response,
    "gemini": _gemini_response,
    "anthropic": _anthropic_response,
}


def _by_chain(request: httpx.Request, n: int) -> httpx.Response:
    """체인 순서대로 그 벤더의 정상 응답을 돌려준다."""
    return _RESPONSES[_CHAIN[min(n - 1, len(_CHAIN) - 1)][0]]()


def _always(code: int):
    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(code, text="provider says no")

    return handler


# ── 어댑터: 세 응답 형식 파싱 (봉투가 다르고 결과는 같다) ──
@pytest.mark.parametrize("vendor", sorted(_RESPONSES))
async def test_each_vendor_envelope_parses_to_the_same_result(vendor):
    """봉투 셋이 같은 `JudgeResult`로 환원되어야 폴백이 결과를 바꾸지 않는다."""
    sleep = _SleepRecorder()
    client, seen = _client(
        lambda _r, _n: _RESPONSES[vendor](), sleep, chain=((vendor, "m"),)
    )

    result = await client.judge("본문", _CANDS)

    assert [(s.keyword_id, s.confidence) for s in result.selected] == [(101, 0.9)]
    assert result.unmatched_concepts == ["개념"]
    assert result.model == "m"          # 어느 모델이 답했는지 결과가 들고 온다
    assert len(seen) == 1 and sleep.delays == []


@pytest.mark.parametrize(
    "vendor, path, header, value",
    [
        ("openai", "/api.openai.com/v1/chat/completions", "authorization", f"Bearer {_KEY}"),
        ("gemini", "/generativelanguage.googleapis.com/v1beta/models/m:generateContent",
         "x-goog-api-key", _KEY),
        ("anthropic", "/api.anthropic.com/v1/messages", "x-api-key", _KEY),
    ],
)
async def test_each_vendor_uses_its_own_path_and_auth_header(vendor, path, header, value):
    """GMS는 프로바이더별 **네이티브** 경로·인증을 통과시킨다 — 헤더를 잘못 쓰면 401이다."""
    sleep = _SleepRecorder()
    client, seen = _client(
        lambda _r, _n: _RESPONSES[vendor](), sleep, chain=((vendor, "m"),)
    )

    await client.judge("본문", _CANDS)

    assert str(seen[0].url) == f"{_ROOT}{path}"
    assert seen[0].headers[header] == value


async def test_anthropic_sends_the_version_header_and_forces_the_tool_call():
    """`anthropic-version`이 없으면 400이고, `tool_choice`가 없으면 산문 응답이 온다."""
    sleep = _SleepRecorder()
    client, seen = _client(
        lambda _r, _n: _anthropic_response(), sleep, chain=(("anthropic", "m"),)
    )

    await client.judge("본문", _CANDS)

    body = json.loads(seen[0].content)
    assert seen[0].headers["anthropic-version"] == "2023-06-01"
    assert body["tool_choice"] == {"type": "tool", "name": body["tools"][0]["name"]}


async def test_openai_schema_is_strict_and_closed():
    """strict 모드는 `additionalProperties: false`와 전 property required를 요구한다.
    하나라도 빠지면 400 — 400은 영구 오류라 폴백 없이 판정이 죽는다."""
    sleep = _SleepRecorder()
    client, seen = _client(lambda _r, _n: _openai_response(), sleep, chain=(("openai", "m"),))

    await client.judge("본문", _CANDS)

    schema = json.loads(seen[0].content)["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == set(schema["schema"]["properties"])
    item = schema["schema"]["properties"]["selected"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


async def test_gemini_keeps_thinking_off():
    """thinking을 켜면 지연·토큰이 늘고 2.5-flash에서 구조화 출력이 흔들린다(테스트 C-2)."""
    sleep = _SleepRecorder()
    client, seen = _client(lambda _r, _n: _gemini_response(), sleep, chain=(("gemini", "m"),))

    await client.judge("본문", _CANDS)

    config = json.loads(seen[0].content)["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert config["responseMimeType"] == "application/json"


# ── 폴백: 넘어가는 조건과 넘어가지 않는 조건 ──────────────
async def test_429_on_the_primary_falls_back_and_succeeds():
    """완료 조건 그 자체 — 1순위 429면 2순위로 넘어가 판정이 성공한다.

    쿼터는 프로바이더 경로별로 걸린다(2026-07-30 실측: 같은 시각 Gemini만 429).
    """
    sleep = _SleepRecorder()

    def handler(request: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(429, text="rate limited") if n == 1 else _by_chain(request, n)

    client, seen = _client(handler, sleep)

    result = await client.judge("본문", _CANDS)

    assert result.model == "second-model"          # 2순위가 답했다
    assert len(seen) == 2
    assert str(seen[0].url).endswith("/chat/completions")           # openai
    assert str(seen[1].url).endswith(":generateContent")            # gemini


async def test_401_does_not_fall_back():
    """키·설정 문제는 다른 벤더에서도 같은 답이다 — 넘어가면 GMS 호출만 3배가 된다."""
    sleep = _SleepRecorder()
    client, seen = _client(_always(401), sleep)

    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)

    assert len(seen) == 1 and sleep.delays == []


@pytest.mark.parametrize("code", [400, 403])
async def test_other_permanent_codes_do_not_fall_back(code):
    sleep = _SleepRecorder()
    client, seen = _client(_always(code), sleep)

    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)

    assert len(seen) == 1


async def test_all_vendors_transient_ends_as_transient():
    """세 벤더 전부 막히면 기존과 같은 분류로 끝난다 — 상태는 PROCESSING에 남고
    재스캔이 회수한다(failure-recovery.md §2.1). 여기서 FAILED로 내리면 그 Context가
    영구히 죽는다."""
    sleep = _SleepRecorder()
    client, seen = _client(_always(429), sleep)

    with pytest.raises(TransientError) as caught:
        await client.judge("본문", _CANDS)

    assert not isinstance(caught.value, PermanentError)
    assert len(seen) == 3
    # 시도 예산이 체인 길이에 곱해지지 않는다 — 3벤더 × 3시도가 아니라 총 3시도다(§3.2).
    assert sleep.delays == [0.5, 1.0]


async def test_each_attempt_uses_the_next_vendor_in_order():
    sleep = _SleepRecorder()
    client, seen = _client(_always(503), sleep)

    with pytest.raises(TransientError):
        await client.judge("본문", _CANDS)

    assert [str(r.url).split("/gmsapi/")[1].split("/")[0] for r in seen] == [
        "api.openai.com",
        "generativelanguage.googleapis.com",
        "api.anthropic.com",
    ]


async def test_error_message_names_the_vendor_that_failed():
    """폴백이 있으면 "어느 경로가 막혔나"가 원인 그 자체다. 모델명은 공개 값이다(P45)."""
    sleep = _SleepRecorder()
    client, _ = _client(_always(429), sleep)

    with pytest.raises(TransientError) as caught:
        await client.judge("본문", _CANDS)

    assert "anthropic:third-model" in str(caught.value)   # 마지막으로 막힌 경로


async def test_schema_violation_falls_back_then_promotes_to_permanent():
    """구조화 출력 방식이 벤더마다 다르므로 위반도 폴백 사유다. 소진 후에는 영구 오류(§2.2)."""
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        # 절단·안전 차단이면 봉투는 오는데 내용이 없다.
        return httpx.Response(200, json={"candidates": [{"content": {"parts": []}}]})

    client, seen = _client(handler, sleep)

    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)

    assert len(seen) == 3


async def test_fallback_recovers_from_a_schema_violation():
    sleep = _SleepRecorder()

    def handler(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(200, json={"choices": []})   # openai 봉투 위반
        return _by_chain(request, n)

    client, seen = _client(handler, sleep)

    result = await client.judge("본문", _CANDS)

    assert result.model == "second-model" and len(seen) == 2


async def test_anthropic_without_a_tool_use_block_is_a_schema_violation():
    """강제 호출을 해도 안전 차단이면 text 블록만 온다. StopIteration이 새면
    분류되지 않은 예외로 service까지 올라가 단계가 PROCESSING에 머문다."""
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "거부합니다"}]})

    client, seen = _client(handler, sleep, chain=(("anthropic", "m"),))

    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)

    assert len(seen) == 3


async def test_type_violation_inside_the_selection_is_a_schema_violation():
    """봉투는 멀쩡한데 값의 타입이 어긋난 경우. 스키마를 줘도 벤더에 따라 통과할 수 있고,
    그때 `int()`가 터진다 — 분류되지 않은 예외로 새면 단계가 PROCESSING에 머문다."""
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return _openai_response({"selected": [{"keywordId": "일백일", "confidence": 0.9}]})

    client, seen = _client(handler, sleep, chain=(("openai", "m"),))

    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)

    assert len(seen) == 3


async def test_selection_that_is_not_an_object_is_a_schema_violation():
    """모델이 객체 대신 배열을 낸 경우. `data.get`이 없으니 그대로 두면 AttributeError다."""
    sleep = _SleepRecorder()

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "[101]"}}]})

    client, seen = _client(handler, sleep, chain=(("openai", "m"),))

    with pytest.raises(PermanentError):
        await client.judge("본문", _CANDS)

    assert len(seen) == 3


async def test_transport_failure_also_falls_back():
    """타임아웃·연결 실패는 프로바이더 경로 하나만 죽은 상태일 수 있다."""
    sleep = _SleepRecorder()

    def handler(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            raise httpx.ConnectError("dns failure", request=request)
        return _by_chain(request, n)

    client, seen = _client(handler, sleep)

    assert (await client.judge("본문", _CANDS)).model == "second-model"
    assert len(seen) == 2


# ── 단일 벤더로 되돌리기 (설정만으로) ─────────────────────
async def test_single_vendor_chain_retries_the_same_vendor():
    """폴백 이전 동작과 같아야 한다 — 체인을 하나로 줄이는 것이 롤백 수단이다."""
    sleep = _SleepRecorder()
    client, seen = _client(_always(429), sleep, chain=(("gemini", "only"),))

    with pytest.raises(TransientError):
        await client.judge("본문", _CANDS)

    assert len(seen) == 3
    assert all(str(r.url).endswith("models/only:generateContent") for r in seen)
    assert sleep.delays == [0.5, 1.0]


async def test_chain_longer_than_attempts_stops_at_the_attempt_budget():
    """§3.2의 상한은 시도 횟수가 지킨다. 체인이 길어도 예산을 넘겨 부르지 않는다."""
    sleep = _SleepRecorder()
    client, seen = _client(_always(503), sleep, attempts=2)

    with pytest.raises(TransientError):
        await client.judge("본문", _CANDS)

    assert len(seen) == 2          # 3순위는 부르지 않는다


# ── 체인 해석 ─────────────────────────────────────────────
def test_unknown_vendor_is_rejected_at_construction():
    """지원 여부는 어댑터 레지스트리만 안다. 기동 시 터져야 조용히 죽지 않는다."""
    with pytest.raises(ValueError, match="지원하지 않는 판정 벤더"):
        resolve_chain([("openai", "m"), ("bedrock", "m")])


def test_empty_chain_is_rejected():
    with pytest.raises(ValueError, match="비어 있다"):
        resolve_chain([])


def test_resolve_chain_preserves_order():
    calls = resolve_chain(_CHAIN)
    assert [c.label for c in calls] == [f"{v}:{m}" for v, m in _CHAIN]


# ── 토큰 로그: 어느 벤더가 답했는지 ───────────────────────
@pytest.mark.parametrize(
    "vendor, prompt, output, total",
    [
        ("openai", 821, 15, 836),
        ("gemini", 732, 21, 753),
        ("anthropic", 1973, 57, 2030),   # Anthropic은 합계를 주지 않아 더해서 남긴다
    ],
)
async def test_token_log_records_the_vendor_and_model(
    tmp_path, monkeypatch, vendor, prompt, output, total
):
    """완료 조건 — 어느 벤더가 응답했는지 토큰 로그로 확인된다."""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("PINLOG_TOKEN_LOG", str(log))
    sleep = _SleepRecorder()
    client, _ = _client(lambda _r, _n: _RESPONSES[vendor](), sleep, chain=((vendor, "m"),))

    await client.judge("본문", _CANDS)

    (row,) = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert row["kind"] == "judge"
    assert row["vendor"] == vendor and row["model"] == "m"
    assert (row["prompt"], row["output"], row["total"]) == (prompt, output, total)


async def test_token_log_shows_which_vendor_answered_after_a_fallback(tmp_path, monkeypatch):
    """폴백이 실제로 발동했는지를 사후에 확인할 수 있는 유일한 기록이다."""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("PINLOG_TOKEN_LOG", str(log))
    sleep = _SleepRecorder()

    def handler(request: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(429, text="x") if n == 1 else _by_chain(request, n)

    client, _ = _client(handler, sleep)

    await client.judge("본문", _CANDS)

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    # 429는 본문이 없으므로 기록되지 않는다 — 남는 것은 성공한 호출 하나다.
    assert [(r["vendor"], r["model"]) for r in rows] == [("gemini", "second-model")]


def test_every_adapter_has_a_token_extractor():
    """레지스트리와 토큰 추출표가 갈라지면 그 벤더 호출의 토큰이 조용히 None이 된다."""
    assert set(ADAPTERS) == set(_JUDGE_TOKENS)

"""검색 결과 LLM 관련도 재판정 클라이언트 — 벤더 어댑터·폴백·계약 (4번째 검색 신호).

`test_llm_vendors.py`와 같은 계층이다(client 자신의 HTTP 계층, DB·Docker 불필요).
`httpx.MockTransport`로 응답 봉투를 직접 만들고 `RetryPolicy.sleep`은 기록만 한다.

고정하는 계약은 넷이다.

    ① 후보 전체를 **한 번의 HTTP 요청**으로 판정한다 — 후보 수만큼 호출하지 않는다
    ② 일시 오류(429 등)는 다음 벤더로 넘어간다(판정 클라이언트와 같은 폴백 규칙)
    ③ 재시도 소진 후 스키마 위반은 영구 오류로 승격한다
    ④ 요청한 contextId 목록 밖의 값이 응답에 섞여 있으면 그 항목만 버린다
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.client.relevance_client import RelevanceJudgeClient
from app.client.retry import RetryPolicy
from app.core.errors import PermanentError, TransientError

_BASE = "https://gms.example/gmsapi/api.openai.com/v1"
_KEY = "gms-key"
_CHAIN = (("openai", "primary-model"), ("gemini", "second-model"))

_CANDS = [
    {"contextId": 101, "placeName": "피치플레이헬스", "body": "싸피 2학기 동안 다니던 헬스장"},
    {"contextId": 102, "placeName": "MH토탈휘트니스", "body": "군대 전역하고 다닌 헬스장"},
]


class _SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _client(handler, sleep, chain=_CHAIN, **kw):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    client = RelevanceJudgeClient(
        gms_base_url=_BASE,
        api_key=_KEY,
        chain=chain,
        timeout=5.0,
        retry=RetryPolicy(sleep=sleep, jitter=lambda d: d, **kw),
        transport=httpx.MockTransport(wrapped),
    )
    return client, seen


def _openai_response(results: list[dict]) -> httpx.Response:
    body = json.dumps({"results": results})
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": body}}]},
    )


def _gemini_response(results: list[dict]) -> httpx.Response:
    body = json.dumps({"results": results})
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": body}]}}]},
    )


@pytest.mark.anyio
async def test_single_call_judges_the_whole_candidate_list():
    """① 후보 2건이 HTTP 요청 1건으로 판정된다 — 후보 수만큼 호출하지 않는다."""
    results = [
        {"contextId": 101, "relevance": "VERY_RELEVANT"},
        {"contextId": 102, "relevance": "NOT_RELEVANT"},
    ]

    def handler(request, call_no):
        assert call_no == 1
        return _openai_response(results)

    client, seen = _client(handler, _SleepRecorder())
    out = await client.judge("싸피 다녔던 헬스장", _CANDS)

    assert len(seen) == 1
    assert out == results


@pytest.mark.anyio
async def test_transient_falls_back_to_next_vendor():
    """② 429는 다음 벤더로 넘어간다."""
    results = [{"contextId": 101, "relevance": "RELEVANT"}]

    def handler(request, call_no):
        if call_no == 1:
            return httpx.Response(429, json={})
        return _gemini_response(results)

    sleep = _SleepRecorder()
    client, seen = _client(handler, sleep)
    out = await client.judge("질의", _CANDS[:1])

    assert len(seen) == 2
    assert "generativelanguage" in str(seen[1].url)
    assert out == results


@pytest.mark.anyio
async def test_schema_violation_exhausted_becomes_permanent():
    """③ 재시도 소진 후에도 스키마 위반이면 영구 오류다."""

    def handler(request, call_no):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    client, seen = _client(handler, _SleepRecorder())
    with pytest.raises(PermanentError):
        await client.judge("질의", _CANDS)
    assert len(seen) == 3  # RetryPolicy 기본 attempts


@pytest.mark.anyio
async def test_connection_failure_is_transient():
    """HTTP 자체가 실패하면(연결 거부 등) TransientError — back 의 강등 대상이다."""

    def handler(request, call_no):
        raise httpx.ConnectError("refused")

    client, seen = _client(handler, _SleepRecorder())
    with pytest.raises(TransientError):
        await client.judge("질의", _CANDS)


@pytest.mark.anyio
async def test_unrequested_context_id_is_dropped():
    """④ 요청 밖 contextId 는 버린다 — 모델이 지어낸 값이 back 으로 새지 않는다."""
    results = [
        {"contextId": 101, "relevance": "VERY_RELEVANT"},
        {"contextId": 999, "relevance": "RELEVANT"},  # 요청에 없던 id
    ]

    def handler(request, call_no):
        return _openai_response(results)

    client, seen = _client(handler, _SleepRecorder())
    out = await client.judge("질의", _CANDS)

    assert out == [{"contextId": 101, "relevance": "VERY_RELEVANT"}]


@pytest.mark.anyio
async def test_unknown_relevance_label_is_dropped():
    """모델이 4단계 밖의 값을 내면(환각) 그 항목만 버린다 — 스키마 위반으로 전체를
    죽이지 않는다. 요청한 나머지 항목은 정상 판정된다."""
    results = [
        {"contextId": 101, "relevance": "SOMEWHAT_RELEVANT"},  # 4단계 밖
        {"contextId": 102, "relevance": "NOT_RELEVANT"},
    ]

    def handler(request, call_no):
        return _openai_response(results)

    client, seen = _client(handler, _SleepRecorder())
    out = await client.judge("질의", _CANDS)

    assert out == [{"contextId": 102, "relevance": "NOT_RELEVANT"}]

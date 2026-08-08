"""POST /internal/v1/search/judge — 엔드포인트 계약 (4번째 검색 신호).

`test_api.py`와 같은 조립 방식(lifespan 우회, `app.state`에 직접 주입)이나 이 엔드포인트는
DB·Preset 캐시를 쓰지 않아 그 두 상태는 필요 없다. 고정하는 계약은 셋이다.

    ① 정상 요청 → 클라이언트 판정을 그대로 응답 형태로 옮긴다
    ② 인증 헤더 없으면 401 (기존 SharedSecretMiddleware, 신규 라우트에도 적용됨을 확인)
    ③ 클라이언트가 TransientError/PermanentError 를 던지면 기존 예외 핸들러가 5xx 로
       매핑한다 — back 은 이 5xx 를 강등 신호로 받는다
"""
from __future__ import annotations

import httpx
import pytest

from app.core.errors import PermanentError, TransientError
from app.main import create_app

HDR = {"X-Internal-Secret": "test-secret"}


class _FakeRelevanceClient:
    def __init__(self, result=None, error: Exception | None = None):
        self._result = result or []
        self._error = error
        self.calls: list[tuple[str, list[dict]]] = []

    async def judge(self, query: str, candidates: list[dict]) -> list[dict]:
        self.calls.append((query, candidates))
        if self._error is not None:
            raise self._error
        return self._result


def _client(fake, settings):
    app = create_app()
    app.state.relevance_judge_client = fake
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


_BODY = {
    "query": "싸피 다녔던 헬스장",
    "candidates": [
        {"contextId": 101, "placeName": "피치플레이헬스", "body": "싸피 2학기 동안 다니던 헬스장"},
        {"contextId": 102, "placeName": "MH토탈휘트니스", "body": "군대 전역하고 다닌 헬스장"},
    ],
}


@pytest.mark.anyio
async def test_judge_endpoint_returns_client_results(settings):
    fake = _FakeRelevanceClient(
        result=[
            {"contextId": 101, "relevance": "VERY_RELEVANT"},
            {"contextId": 102, "relevance": "NOT_RELEVANT"},
        ]
    )
    async with _client(fake, settings) as client:
        resp = await client.post("/internal/v1/search/judge", json=_BODY, headers=HDR)

    assert resp.status_code == 200
    assert resp.json() == {
        "results": [
            {"contextId": 101, "relevance": "VERY_RELEVANT"},
            {"contextId": 102, "relevance": "NOT_RELEVANT"},
        ]
    }
    assert fake.calls == [(_BODY["query"], _BODY["candidates"])]


@pytest.mark.anyio
async def test_judge_endpoint_requires_secret_header(settings):
    fake = _FakeRelevanceClient()
    async with _client(fake, settings) as client:
        resp = await client.post("/internal/v1/search/judge", json=_BODY)

    assert resp.status_code == 401
    assert fake.calls == []


@pytest.mark.anyio
async def test_judge_endpoint_maps_transient_error_to_503(settings):
    fake = _FakeRelevanceClient(error=TransientError("gms down"))
    async with _client(fake, settings) as client:
        resp = await client.post("/internal/v1/search/judge", json=_BODY, headers=HDR)

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_judge_endpoint_maps_permanent_error_to_502(settings):
    fake = _FakeRelevanceClient(error=PermanentError("schema violation"))
    async with _client(fake, settings) as client:
        resp = await client.post("/internal/v1/search/judge", json=_BODY, headers=HDR)

    assert resp.status_code == 502


@pytest.mark.anyio
async def test_judge_endpoint_rejects_empty_candidates(settings):
    fake = _FakeRelevanceClient()
    async with _client(fake, settings) as client:
        resp = await client.post(
            "/internal/v1/search/judge",
            json={"query": "질의", "candidates": []},
            headers=HDR,
        )

    assert resp.status_code == 422
    assert fake.calls == []

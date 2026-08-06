"""검색 질의 LLM 재작성 — 강등·캐시·플래그 off 계약 (S15P11A705-337).

고정하는 계약은 넷이다.

    ① 플래그 off(기본값)면 재작성 클라이언트가 호출되지 않고 원문이 임베딩된다
       — 현행 검색과 동작이 같다
    ② 재작성 성공이면 재작성문이 임베딩되고 컷 판정도 재작성문 기준이다
    ③ 재작성 실패(일시·영구)는 오류가 아니라 강등이다 — 원문으로 검색이 계속된다
    ④ 같은 질의는 캐시로 같은 재작성을 받는다 — LLM 을 한 번만 부른다

DB 는 가짜 커넥션으로 대체한다 — 여기서 재는 것은 재작성 경로이지 SQL 이 아니다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.client.retry import RetryPolicy
from app.client.rewrite_client import RewriteClient
from app.core.config import Settings
from app.core.errors import PermanentError, TransientError
from app.service.search_service import SearchService
from tests.test_unit import _ENV  # noqa: F401  (환경 키 한 벌을 재사용한다)


def _settings(monkeypatch, **overrides) -> Settings:
    from tests.test_unit import _ENV as env
    for k, v in {**env, **overrides}.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


class _FakeEmbedding:
    def __init__(self):
        self.calls: list[str] = []

    async def embed_one(self, text: str):
        self.calls.append(text)
        return [0.0] * 4


class _FakeRewrite:
    """RewriteClient 자리에 꽂는 가짜 — 호출 수와 동작을 제어한다."""

    def __init__(self, result: str | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0

    async def rewrite(self, query: str) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else query


class _FakeDb:
    """`acquire()` 만 흉내낸다. repo.search 는 monkeypatch 로 비운다."""

    @asynccontextmanager
    async def acquire(self):
        yield None


@pytest.fixture
def no_rows(monkeypatch):
    async def _empty(conn, user_id, profile, embedding, limit):
        return []

    monkeypatch.setattr(
        "app.service.search_service.context_embedding_repo.search", _empty
    )


PROFILE = "openai-text-embedding-3-small-1536-cosine-v1"


async def _search(service):
    return await service.search(1, "부캠", 20, PROFILE)


@pytest.mark.anyio
async def test_flag_off_never_calls_rewrite(monkeypatch, no_rows):
    """① 기본값(off)에서 재작성은 호출 0회, 임베딩 입력은 원문이다."""
    settings = _settings(monkeypatch)
    assert settings.search_llm_enabled is False
    emb, rw = _FakeEmbedding(), _FakeRewrite(result="부트캠프")
    service = SearchService(_FakeDb(), emb, settings, rewrite_client=rw)
    await _search(service)
    assert rw.calls == 0
    assert emb.calls == ["부캠"]


@pytest.mark.anyio
async def test_flag_on_embeds_rewritten_query(monkeypatch, no_rows):
    """② 성공 시 재작성문이 임베딩된다."""
    settings = _settings(monkeypatch, SEARCH_LLM_ENABLED="true")
    emb, rw = _FakeEmbedding(), _FakeRewrite(result="부트캠프")
    service = SearchService(_FakeDb(), emb, settings, rewrite_client=rw)
    await _search(service)
    assert rw.calls == 1
    assert emb.calls == ["부트캠프"]


@pytest.mark.anyio
@pytest.mark.parametrize("error", [TransientError("t"), PermanentError("p")])
async def test_failure_degrades_to_original_query(monkeypatch, no_rows, error):
    """③ 실패는 강등 — 응답이 실패하지 않고 원문으로 검색한다."""
    settings = _settings(monkeypatch, SEARCH_LLM_ENABLED="true")
    emb, rw = _FakeEmbedding(), _FakeRewrite(error=error)
    service = SearchService(_FakeDb(), emb, settings, rewrite_client=rw)
    result = await _search(service)
    assert result == []
    assert emb.calls == ["부캠"]


@pytest.mark.anyio
async def test_flag_on_without_client_uses_original(monkeypatch, no_rows):
    """클라이언트 미주입이면 플래그가 켜져 있어도 원문 경로다 — 조립 실수의 방어선."""
    settings = _settings(monkeypatch, SEARCH_LLM_ENABLED="true")
    emb = _FakeEmbedding()
    service = SearchService(_FakeDb(), emb, settings, rewrite_client=None)
    await _search(service)
    assert emb.calls == ["부캠"]


def test_cut_follows_rewritten_query_band(monkeypatch):
    """② 컷의 단어형/문장형 분기는 임베딩된 텍스트를 따라간다.

    `부캠`(단어형 0.24)이 `신한 부트캠프`(문장형 0.30)로 재작성되면, 0.27 은
    단어형 하한은 넘지만 문장형 하한에 걸려야 한다 — 판정 입력과 임베딩 입력이
    갈리면 실측 근거(대역이 임베딩 텍스트를 따른다)가 무너진다.
    """
    settings = _settings(monkeypatch)
    service = SearchService(None, None, settings)
    rows = [{"similarity": 0.27}]
    assert service._cut(rows, "부캠") == rows           # 단어형 0.24 통과
    assert service._cut(rows, "신한 부트캠프") == []     # 문장형 0.30 탈락


# ── RewriteClient 자체 — 캐시·빈 결과·폭주 방어 ──────────────────────────────
#
# HTTP 는 transport 스텁으로 자른다(다른 클라이언트 테스트와 같은 이음새).

import httpx  # noqa: E402


def _client_with(responses: list[str], **kw) -> tuple[RewriteClient, list]:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.path)
        body = responses[min(len(hits) - 1, len(responses) - 1)]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": body}}]
        })

    client = RewriteClient(
        "https://gms.example/gmsapi/api.openai.com/v1",
        "key",
        [("openai", "gpt-4o-mini")],
        timeout=1.0,
        retry=RetryPolicy(attempts=1),
        transport=httpx.MockTransport(handler),
        **kw,
    )
    return client, hits


@pytest.mark.anyio
async def test_cache_returns_same_result_without_second_call():
    """④ 같은 질의 두 번 → HTTP 1회. 값도 같다."""
    client, hits = _client_with(['{"query": "부트캠프"}'])
    first = await client.rewrite("부캠")
    second = await client.rewrite("부캠")
    assert first == second == "부트캠프"
    assert len(hits) == 1


@pytest.mark.anyio
async def test_blank_rewrite_falls_back_to_original():
    """빈 결과는 원문이다 — 검색어가 통째로 사라지는 것을 막는다."""
    client, _ = _client_with(['{"query": "   "}'])
    assert await client.rewrite("부캠") == "부캠"


@pytest.mark.anyio
async def test_oversized_rewrite_falls_back_to_original():
    """원문의 4배를 넘는 재작성은 모델이 개념을 덧붙인 것 — 버리고 원문을 쓴다."""
    client, _ = _client_with([f'{{"query": "{"부트캠프 근처 맛집과 카페 그리고 " * 8}"}}'])
    assert await client.rewrite("부캠") == "부캠"

"""API 계층 — 실제 DB + Fake client. 202·검색 형식·422·401."""
from __future__ import annotations

import httpx
import pytest_asyncio

from app.cache.preset_cache import PresetCache
from app.main import create_app
from app.service.context_processing import ContextProcessingService
from app.service.embedding_service import EmbeddingService
from app.service.keyword_service import KeywordService
from app.service.search_service import SearchService
from tests.builders import make_embedding, make_state
from tests.fakes import FakeEmbeddingClient, FakeLLMClient, deterministic_vector

HDR = {"X-Internal-Secret": "test-secret"}


@pytest_asyncio.fixture
async def api(db, settings):
    """lifespan을 우회하고 app.state에 Fake client·서비스를 직접 주입."""
    app = create_app()
    fake_emb = FakeEmbeddingClient()
    fake_llm = FakeLLMClient()
    cache = PresetCache()
    cache.load([])
    app.state.settings = settings
    app.state.db = db
    app.state.embedding_client = fake_emb
    app.state.llm_client = fake_llm
    app.state.preset_cache = cache
    app.state.search_service = SearchService(db, fake_emb, settings)
    app.state.context_processing_service = ContextProcessingService(
        db,
        EmbeddingService(db, fake_emb, settings),
        KeywordService(db, fake_llm, cache, settings),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_health_ok(api):
    r = await api.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_process_returns_202(api):
    r = await api.post(
        "/internal/v1/context/process",
        headers=HDR,
        json={"contextId": 1, "userId": 1, "recordId": 1, "text": "t"},
    )
    assert r.status_code == 202


async def test_search_returns_context_id(api, conn, settings):
    profile = settings.embedding_profile
    await make_state(conn, context_id=5, embedding_status="COMPLETED", keyword_status="COMPLETED")
    await make_embedding(conn, context_id=5, user_id=1, record_id=50,
                         embedding_profile=profile, embedding=deterministic_vector("hello"))
    r = await api.post(
        "/internal/v1/search",
        headers=HDR,
        json={"userId": 1, "query": "hello", "limit": 10, "embeddingProfile": profile},
    )
    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["recordId"] == 50 and item["contextId"] == 5 and "similarity" in item


async def test_search_profile_mismatch_422(api):
    r = await api.post(
        "/internal/v1/search",
        headers=HDR,
        json={"userId": 1, "query": "x", "limit": 5, "embeddingProfile": "wrong-profile-v9"},
    )
    assert r.status_code == 422


async def test_missing_internal_secret_401(api, settings):
    r = await api.post(
        "/internal/v1/search",
        json={
            "userId": 1,
            "query": "x",
            "limit": 5,
            "embeddingProfile": settings.embedding_profile,
        },
    )
    assert r.status_code == 401

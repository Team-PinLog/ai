"""API 계층 — 실제 DB + Fake client. 202·검색 형식·422·401·프로브(/health·/ready)."""
from __future__ import annotations

from contextlib import asynccontextmanager

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


def _preset_row(preset_id: int = 1) -> dict:
    """PresetCache.load()가 받는 DB 행 모양. /ready의 preset 조건(≥1건)용."""
    return {
        "id": preset_id,
        "code": "PROBE",
        "display_name": "조용한",
        "category": "MOOD",
        "description": "소음이 적고 차분한 분위기",
        "examples": [],
        "visibility": "PUBLIC",
        "version": 1,
        "embedding": deterministic_vector("quiet"),
    }


@asynccontextmanager
async def _api_client(db, settings, preset_rows: list[dict]):
    app = create_app()
    fake_emb = FakeEmbeddingClient()
    fake_llm = FakeLLMClient()
    cache = PresetCache()
    cache.load(preset_rows)
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


@pytest_asyncio.fixture
async def api(db, settings):
    """lifespan을 우회하고 app.state에 Fake client·서비스를 직접 주입. Preset 캐시는 비어 있다."""
    async with _api_client(db, settings, []) as client:
        yield client


@pytest_asyncio.fixture
async def ready_api(db, settings):
    """/ready 성공 분기용 — Preset 캐시에 1건 적재한 것 외에는 api와 동일."""
    async with _api_client(db, settings, [_preset_row()]) as client:
        yield client


async def test_health_ok(api):
    r = await api.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# ── 프로브 계약 (ai#32 §2) ─────────────────────────────
async def test_ready_200_when_db_and_presets_ok(ready_api):
    r = await ready_api.get("/ready")
    assert r.status_code == 200 and r.json() == {"status": "ready"}


async def test_ready_503_when_preset_cache_empty(api):
    r = await api.get("/ready")
    assert r.status_code == 503 and r.json() == {"status": "not_ready"}


async def test_ready_503_when_db_unreachable(ready_api, db):
    await db.disconnect()  # 기동은 성공했지만 이후 풀이 죽은 상황
    r = await ready_api.get("/ready")
    assert r.status_code == 503 and r.json() == {"status": "not_ready"}


async def test_ready_needs_no_internal_secret(ready_api):
    """프로브는 헤더 없이 호출한다. /internal/ 밖이므로 미들웨어를 타지 않아야 한다."""
    r = await ready_api.get("/ready")  # HDR 미첨부
    assert r.status_code != 401


async def test_ready_exposes_no_configured_values(ready_api, settings):
    """credential·endpoint·profile 값이 응답 어디에도 없어야 한다(무인증 경로)."""
    body = (await ready_api.get("/ready")).text
    for value in (
        settings.gms_base_url,
        settings.gms_api_key,
        settings.internal_shared_secret,
        settings.database_url,
        settings.embedding_profile,
    ):
        assert value not in body


async def test_health_stays_ok_while_not_ready(api):
    """/health는 liveness 전용 — 준비되지 않아도 정적 200이다(동작 변경 금지 합의)."""
    assert (await api.get("/ready")).status_code == 503
    r = await api.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


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

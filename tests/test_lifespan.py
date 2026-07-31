"""기동 조립 — `app.main.lifespan` (keyword-preset.md §2, architecture.md §5).

`test_api.py` 는 lifespan 을 **우회하고** `app.state` 에 Fake 를 직접 꽂는다. 그래서 실제
기동 경로(풀 생성 → 클라이언트 조립 → Preset 캐시 적재 → 0건이면 기동 중단)는 기준선에서
한 줄도 실행되지 않았다(`S15P11A705-110`).

여기서 검증하는 것은 **조립과 중단 조건**이다. 실제 DB 를 쓰고, 외부 클라이언트는 생성만
하고 호출하지 않는다 — 생성자는 IO 를 하지 않으므로 여기서 Fake 로 바꾸면 오히려
"lifespan 이 진짜 클라이언트를 조립한다"는 단언을 잃는다(integration-tests.md §4.2 의
Fake 규칙은 파이프라인이 client 를 무엇으로 대체하는가에 대한 것이다).
"""
from __future__ import annotations

import pytest

from app.cache.preset_cache import PresetCache
from app.client.embedding_client import EmbeddingClient
from app.client.llm_client import LLMClient
from app.core.db import Database
from app.main import create_app
from app.service.context_processing import ContextProcessingService
from app.service.search_service import SearchService
from tests.builders import make_preset


async def test_lifespan_assembles_state_and_loads_preset_cache(db, conn, settings):
    await make_preset(
        conn, id=101, code="WITH_FRIENDS", embedding_profile=settings.embedding_profile
    )
    await make_preset(
        conn, id=201, code="CAFE", embedding_profile=settings.embedding_profile
    )

    app = create_app()
    async with app.router.lifespan_context(app):
        state = app.state
        assert state.settings.embedding_profile == settings.embedding_profile
        assert isinstance(state.db, Database)
        # 진짜 클라이언트가 settings 로 조립되는지. Fake 로 바꾸면 볼 수 없는 단언이다.
        assert isinstance(state.embedding_client, EmbeddingClient)
        assert isinstance(state.llm_client, LLMClient)
        assert isinstance(state.preset_cache, PresetCache)
        assert isinstance(state.search_service, SearchService)
        assert isinstance(state.context_processing_service, ContextProcessingService)

        snapshot = state.preset_cache.snapshot()
        assert {p.id for p in snapshot.presets} == {101, 201}

        # 풀이 실제로 살아 있어야 한다 — connect() 를 부르고 예외를 삼키면 여기서 드러난다.
        async with state.db.acquire() as live:
            assert await live.fetchval("SELECT 1") == 1


async def test_lifespan_aborts_when_no_preset_matches_the_server_profile(db, conn, settings):
    """Preset 이 있어도 Profile 이 다르면 0건이다 — 부트스트랩을 다른 Profile 로 돌린 상태.

    이걸 통과시키면 서버는 후보 0개로 계속 COMPLETED 를 쓰며 데이터를 조용히 망친다
    (keyword-preset.md §2). 기동 실패가 계약이다.
    """
    await make_preset(conn, id=101, code="WITH_FRIENDS", embedding_profile="other-profile-v9")

    app = create_app()
    with pytest.raises(RuntimeError, match="Keyword Preset 적재 0건"):
        async with app.router.lifespan_context(app):
            pass


async def test_lifespan_aborts_when_every_preset_is_blocked(db, conn, settings):
    """BLOCKED 만 남은 DB. 행은 있지만 캐시 적재 건수는 0이므로 역시 기동 실패다 —
    조건이 `rows` 가 아니라 `loaded` 여야 하는 이유."""
    await make_preset(
        conn,
        id=101,
        code="WITH_FRIENDS",
        embedding_profile=settings.embedding_profile,
        visibility="BLOCKED",
    )

    app = create_app()
    with pytest.raises(RuntimeError, match="Keyword Preset 적재 0건"):
        async with app.router.lifespan_context(app):
            pass


async def test_lifespan_disconnects_the_pool_on_shutdown(db, conn, settings):
    await make_preset(
        conn, id=101, code="WITH_FRIENDS", embedding_profile=settings.embedding_profile
    )

    app = create_app()
    async with app.router.lifespan_context(app):
        database = app.state.db
        assert database.pool is not None

    # 종료 후 풀 접근은 실패해야 한다. 남으면 재기동마다 커넥션이 쌓인다.
    with pytest.raises(RuntimeError, match="pool not initialized"):
        _ = database.pool

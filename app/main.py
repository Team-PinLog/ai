"""FastAPI 인스턴스, lifespan, 라우터 등록.

lifespan startup에서 DB 풀·임베딩 클라이언트·Preset 캐시를 조립한다. Preset 적재가
0건이면 기동을 실패시킨다(keyword-preset.md §2 — Preset 없이 뜬 서버는 조용히 데이터를
망친다).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.cache.preset_cache import PresetCache
from app.client.embedding_client import EmbeddingClient
from app.client.llm_client import LLMClient
from app.core.config import get_settings
from app.core.db import Database
from app.core.errors import ProfileMismatchError
from app.core.logging import configure_logging, get_logger
from app.core.security import SharedSecretMiddleware
from app.repository import keyword_preset_repo
from app.service.context_processing import ContextProcessingService
from app.service.embedding_service import EmbeddingService
from app.service.keyword_service import KeywordService
from app.service.search_service import SearchService

log = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()

    db = Database(settings.database_url)
    await db.connect()

    embedding_client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    llm_client = LLMClient(
        gms_base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.judge_model,
    )

    preset_cache = PresetCache()
    async with db.acquire() as conn:
        rows = await keyword_preset_repo.load_active(conn, settings.embedding_profile)
    loaded = preset_cache.load(rows)
    if loaded == 0:
        raise RuntimeError(
            "Keyword Preset 적재 0건 — 부트스트랩(load_presets) 미실행이거나 "
            f"Profile 불일치(profile={settings.embedding_profile}). 기동 중단."
        )
    log.info("preset cache loaded: %d presets", loaded)

    embedding_service = EmbeddingService(db, embedding_client, settings)
    keyword_service = KeywordService(db, llm_client, preset_cache, settings)

    app.state.settings = settings
    app.state.db = db
    app.state.embedding_client = embedding_client
    app.state.llm_client = llm_client
    app.state.preset_cache = preset_cache
    app.state.search_service = SearchService(db, embedding_client, settings)
    app.state.context_processing_service = ContextProcessingService(
        db, embedding_service, keyword_service
    )

    try:
        yield
    finally:
        await db.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PinLog AI", lifespan=lifespan)

    app.add_middleware(
        SharedSecretMiddleware, secret=settings.internal_shared_secret
    )

    @app.exception_handler(ProfileMismatchError)
    async def _profile_mismatch(request: Request, exc: ProfileMismatchError):
        log.warning(
            "profile mismatch: request=%s server=%s",
            exc.request_profile,
            exc.server_profile,
        )
        return JSONResponse(
            status_code=422,
            content={
                "detail": "embeddingProfile mismatch",
                "requestProfile": exc.request_profile,
                "serverProfile": exc.server_profile,
            },
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    from app.api.internal.v1 import context, search

    app.include_router(search.router, prefix="/internal/v1")
    app.include_router(context.router, prefix="/internal/v1")

    return app


app = create_app()

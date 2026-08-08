"""FastAPI 인스턴스, lifespan, 예외 핸들러, 라우터 등록.

lifespan startup에서 DB 풀·임베딩 클라이언트·Preset 캐시를 조립한다. Preset 적재가
0건이면 기동을 실패시킨다(keyword-preset.md §2 — Preset 없이 뜬 서버는 조용히 데이터를
망친다).

예외 핸들러는 **오류 분류를 HTTP 상태로 바꾸는 유일한 지점**이다(failure-recovery.md
§2.5). 이 매핑의 계약 테스트는 `tests/test_api_error_contract.py`이며, 업스트림 상태
코드부터 응답 상태 코드까지 한 요청으로 관통해 고정한다.
"""
from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.cache.preset_cache import PresetCache
from app.client._calls import meter as call_meter
from app.client.embedding_client import EmbeddingClient
from app.client.kakao_local_client import HttpKakaoLocalClient
from app.client.llm_client import LLMClient
from app.client.relevance_client import RelevanceJudgeClient
from app.client.retry import RetryPolicy
from app.client.rewrite_client import RewriteClient
from app.client.vision_client import GmsGeminiVisionClient
from app.core.config import get_settings
from app.core.db import Database
from app.core.db_errors import DatabasePermanentError, DatabaseTransientError
from app.core.errors import PermanentError, ProfileMismatchError, TransientError
from app.core.logging import configure_logging, get_logger
from app.core.place_suggestion import VisionPermanentError, VisionTransientError
from app.core.security import SharedSecretMiddleware
from app.repository import keyword_preset_repo
from app.service.context_processing import ContextProcessingService
from app.service.embedding_service import EmbeddingService
from app.service.keyword_service import KeywordService
from app.service.place_suggestion_service import PlaceSuggestionService
from app.service.search_service import SearchService

log = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    try:
        async with AsyncExitStack() as stack:
            db = Database(settings.database_url)
            await db.connect()
            stack.push_async_callback(db.disconnect)

            place_http = await stack.enter_async_context(
                httpx.AsyncClient(
                    limits=httpx.Limits(
                        max_connections=4,
                        max_keepalive_connections=4,
                    )
                )
            )

            embedding_client = EmbeddingClient(
                base_url=settings.gms_base_url,
                api_key=settings.gms_api_key,
                model=settings.embedding_model,
                dimension=settings.embedding_dimension,
            )
            llm_client = LLMClient(
                gms_base_url=settings.gms_base_url,
                api_key=settings.gms_api_key,
                chain=settings.judge_vendors,
            )
            # 검색 질의 재작성 (S15P11A705-337). 기본 off — SEARCH_LLM_ENABLED 가
            # false 면 SearchService 가 호출하지 않아 검색은 현행과 동일하다.
            # 판정과 같은 벤더 체인을 쓰되 예산(타임아웃·시도)은 검색 전용 값이다.
            rewrite_client = RewriteClient(
                gms_base_url=settings.gms_base_url,
                api_key=settings.gms_api_key,
                chain=settings.judge_vendors,
                timeout=settings.search_llm_timeout_sec,
                retry=RetryPolicy(attempts=settings.search_llm_attempts),
                cache_size=settings.search_rewrite_cache_size,
            )

            # 검색 결과 LLM 관련도 재판정 (4번째 검색 신호). 기본 off — back 은
            # SEARCH_RELEVANCE_JUDGE_ENABLED=false 면 이 엔드포인트를 호출하지 않는다.
            relevance_judge_client = RelevanceJudgeClient(
                gms_base_url=settings.gms_base_url,
                api_key=settings.gms_api_key,
                chain=settings.judge_vendors,
                timeout=settings.search_relevance_judge_timeout_sec,
                retry=RetryPolicy(attempts=settings.search_relevance_judge_attempts),
            )

            preset_cache = PresetCache()
            async with db.acquire() as conn:
                rows = await keyword_preset_repo.load_active(
                    conn, settings.embedding_profile
                )
            loaded = preset_cache.load(rows)
            if loaded == 0:
                raise RuntimeError(
                    "Keyword Preset 적재 0건 — 부트스트랩(load_presets) 미실행이거나 "
                    f"Profile 불일치(profile={settings.embedding_profile}). 기동 중단."
                )
            log.info("preset cache loaded: %d presets", loaded)

            embedding_service = EmbeddingService(db, embedding_client, settings)
            keyword_service = KeywordService(db, llm_client, preset_cache, settings)
            vision_client = GmsGeminiVisionClient(
                place_http,
                gms_base_url=settings.gms_base_url,
                api_key=settings.gms_api_key,
                model=settings.image_model,
                timeout_sec=settings.image_model_timeout_sec,
                max_image_bytes=settings.gms_image_max_bytes,
                max_request_bytes=settings.gms_vision_request_max_bytes,
            )
            kakao_client = HttpKakaoLocalClient(
                place_http,
                api_key=settings.kakao_rest_api_key,
                timeout_sec=settings.kakao_timeout_sec,
            )

            app.state.settings = settings
            app.state.db = db
            app.state.embedding_client = embedding_client
            app.state.llm_client = llm_client
            app.state.preset_cache = preset_cache
            # preset_cache 는 keyword 재정렬(S15P11A705-339)의 질의-Preset 후보용이다.
            # SEARCH_KEYWORD_RERANK_ENABLED=false(기본)면 검색은 현행과 동일하다.
            app.state.search_service = SearchService(
                db, embedding_client, settings,
                rewrite_client=rewrite_client, preset_cache=preset_cache,
            )
            app.state.relevance_judge_client = relevance_judge_client
            app.state.context_processing_service = ContextProcessingService(
                db, embedding_service, keyword_service
            )
            app.state.place_suggestion_service = PlaceSuggestionService(
                vision_client,
                kakao_client,
                max_upload_bytes=settings.image_max_bytes,
                max_concurrency=settings.vision_max_concurrency,
                timeout_sec=settings.place_suggestion_timeout_sec,
                log_results=settings.place_suggestion_log_results,
            )
            yield
    finally:
        # 진행 중인 GMS 집계 창을 마지막으로 한 번 내보낸다. 요약은 다음 호출이 밀어내는
        # 구조라(`_calls.py`), 이것이 없으면 종료 직전 구간이 통째로 사라진다 — 시연이
        # 끝나고 파드를 내리는 순간의 실패율이 그렇게 사라진다.
        call_meter.flush()


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

    # 업스트림 실패를 두 상태 코드로 가른다(failure-recovery.md §2.5). 분류와 재시도는
    # S15P11A705-121이 client 층까지 고쳤지만 **그 예외가 응답이 되는 지점의 계약이
    # 없었고**, 분류된 실패가 전부 500으로 나갔다(ai#69 — 임베딩 502가 검색 500이 된 건).
    #
    # 500을 비워 두는 것이 이 매핑의 목적이다. 분류된 실패까지 500이면 "우리 코드의
    # 결함"을 뜻하는 신호가 사라지고, 로그만 보고는 AI가 깨졌는지 게이트웨이가 깨졌는지
    # 가릴 수 없다. 두 핸들러를 넣은 뒤 남는 500은 **우리 쪽 결함 하나**를 뜻한다.
    #
    # 응답 본문은 고정 문구 한 줄이다. 값은 싣지 않되(예외 메시지에는 업스트림 응답
    # 본문 200자가 들어 있고 — `embedding_client._embed_batch` — 거기에 endpoint·키
    # 힌트가 섞일 수 있다. probe.py가 세운 "credential·endpoint·profile을 어떤
    # 분기에서도 싣지 않는다"를 이 경로에도 적용한다), **층 이름은 가른다**
    # (S15P11A705-229) — `DatabaseTransientError`/`DatabasePermanentError`는
    # `-221`이 둔 하위 타입이라 타입만 보면 DB에서 온 실패와 GMS에서 온 실패가 구분된다.
    # 원인 추적은 `app.client.gms` 계측이 남기는 `status=... outcome=...`가 하고
    # (S15P11A705-197), `back`은 이 본문을 읽지 않는다(`AiSearchClient.translate` —
    # serverProfile을 보는 422 말고는 본문을 버린다).
    @app.exception_handler(TransientError)
    async def _transient(request: Request, exc: TransientError):
        # 타입 이름만 남긴다. 같은 이유로 str(exc)를 찍지 않는다(§2.4 원칙 4).
        log.warning("upstream transient failure: %s", type(exc).__name__)
        detail = (
            "database unavailable"
            if isinstance(exc, DatabaseTransientError)
            else (
                "vision upstream unavailable"
                if isinstance(exc, VisionTransientError)
                else "embedding upstream unavailable"
            )
        )
        return JSONResponse(status_code=503, content={"detail": detail})

    @app.exception_handler(PermanentError)
    async def _permanent(request: Request, exc: PermanentError):
        # 키·모델명·base URL 설정 문제다. 재시도로 풀리지 않으므로 ERROR다(§2.2).
        log.error("upstream permanent failure: %s", type(exc).__name__)
        detail = (
            "database rejected the request"
            if isinstance(exc, DatabasePermanentError)
            else (
                "vision upstream rejected the request"
                if isinstance(exc, VisionPermanentError)
                else "embedding upstream rejected the request"
            )
        )
        return JSONResponse(status_code=502, content={"detail": detail})

    # liveness·startup 전용. 프로세스가 살아 있다는 사실만 답한다 — DB·캐시 상태를
    # 섞지 않는 것이 배포 계약(ai#32 §2)이다. 준비 판정은 /ready가 한다.
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    from app.api import probe
    from app.api.internal.v1 import context, place_suggestion, search

    app.include_router(probe.router)  # /ready — 무인증(프로브가 헤더 없이 호출)
    app.include_router(search.router, prefix="/internal/v1")
    app.include_router(context.router, prefix="/internal/v1")
    app.include_router(place_suggestion.router, prefix="/internal/v1")

    return app


app = create_app()

"""Readiness 프로브 — `GET /ready`.

dev 배포 계약(ai#32 §2)이 정한 판정:

    DB      커넥션 획득 후 SELECT 1 성공
    preset  현재 Embedding Profile 기준 캐시 1건 이상
    성공    200 {"status": "ready"}
    실패    503 {"status": "not_ready"}

**GMS를 호출하지 않는다.** 준비 판정에 외부 게이트웨이 가용성을 섞으면 자기 책임 밖의
장애로 인스턴스가 트래픽에서 빠진다. GMS 도달성은 배포 시점 스모크(`app.smoke`)로
따로 증명한다.

Profile 조건은 캐시 건수로 충족된다 — 캐시는 lifespan이 `settings.embedding_profile`로
조회한 행만 담기 때문에(`main.py`), 1건 이상이면 현재 Profile 기준 1건 이상이다.

`/health`(정적 200, liveness·startup 전용)는 `main.py`에 그대로 둔다.

**응답은 status 한 필드뿐이다.** 이 경로는 `/internal/` 밖이라 공유 시크릿 미들웨어를
타지 않고 무인증으로 노출된다(프로브가 헤더 없이 호출해야 하므로 의도된 것이다).
credential·endpoint·profile 값을 어떤 분기에서도 싣지 않는다.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

log = get_logger("app.api.probe")

router = APIRouter()

_READY = {"status": "ready"}
_NOT_READY = {"status": "not_ready"}


async def _db_reachable(db) -> bool:
    async with db.acquire() as conn:
        return await conn.fetchval("SELECT 1") == 1


def _presets_loaded(preset_cache) -> bool:
    return len(preset_cache.snapshot().presets) > 0


@router.get("/ready")
async def ready(request: Request):
    state = request.app.state
    try:
        healthy = await _db_reachable(state.db) and _presets_loaded(state.preset_cache)
    except Exception as exc:  # noqa: BLE001
        # 원인과 무관하게 not_ready다. 예외 메시지에는 DSN이 섞여 들어올 수 있으므로
        # 타입 이름만 남긴다 — 이 로그도 값 노출 금지 대상이다.
        log.warning("readiness check failed: %s", type(exc).__name__)
        healthy = False

    if not healthy:
        return JSONResponse(status_code=503, content=_NOT_READY)
    return _READY

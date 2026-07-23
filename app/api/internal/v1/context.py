"""POST /internal/v1/context/process — Context AI 처리 접수.

router는 스키마 검증과 백그라운드 작업 등록만 하고 202를 반환한다. 상태 검사·모델
호출·저장은 전부 백그라운드 작업 안에서 실행된다(context-processing.md §1). 완료 통보
웹훅·폴링을 두지 않으며, Spring은 ai.context_ai_state를 직접 조회한다.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from app.schema.context import ContextProcessRequest
from app.service.context_processing import ContextProcessingService

router = APIRouter()


def get_processing_service(request: Request) -> ContextProcessingService:
    return request.app.state.context_processing_service


@router.post("/context/process", status_code=status.HTTP_202_ACCEPTED)
async def process_context(
    req: ContextProcessRequest,
    background: BackgroundTasks,
    request: Request,
) -> Response:
    service = get_processing_service(request)
    background.add_task(service.process, req)
    return Response(status_code=status.HTTP_202_ACCEPTED)

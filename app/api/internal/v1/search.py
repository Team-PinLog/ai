"""POST /internal/v1/search — 개인 자연어 검색.

router는 요청 검증과 서비스 호출만 한다. Profile 불일치의 422 변환은 main.py의
예외 핸들러가 담당한다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.schema.search import SearchRequest, SearchResponse
from app.service.search_service import SearchService

router = APIRouter()


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service


@router.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    results = await service.search(
        req.userId, req.query, req.limit, req.embeddingProfile
    )
    return SearchResponse(results=results)

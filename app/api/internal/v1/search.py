"""POST /internal/v1/search — 개인 자연어 검색.
POST /internal/v1/search/judge — 검색 후보 LLM 관련도 재판정 (4번째 검색 신호).

router는 요청 검증과 서비스 호출만 한다. Profile 불일치의 422 변환은 main.py의
예외 핸들러가 담당한다. /search/judge 의 오류(TransientError·PermanentError)도
같은 핸들러가 5xx 로 매핑한다 — back 은 그 5xx 를 강등 신호로 받는다(원래 순서 유지).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.client.relevance_client import RelevanceJudgeClient
from app.schema.relevance import RelevanceJudgeRequest, RelevanceJudgeResponse
from app.schema.search import SearchRequest, SearchResponse
from app.service.search_service import SearchService

router = APIRouter()


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service


def get_relevance_judge_client(request: Request) -> RelevanceJudgeClient:
    return request.app.state.relevance_judge_client


@router.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    results = await service.search(
        req.userId, req.query, req.limit, req.embeddingProfile
    )
    return SearchResponse(results=results)


@router.post("/search/judge", response_model=RelevanceJudgeResponse)
async def judge(
    req: RelevanceJudgeRequest,
    client: RelevanceJudgeClient = Depends(get_relevance_judge_client),
) -> RelevanceJudgeResponse:
    results = await client.judge(
        req.query, [c.model_dump() for c in req.candidates]
    )
    return RelevanceJudgeResponse(results=results)

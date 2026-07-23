"""개인 검색 API 요청·응답 스키마 (personal-search.md §1, §6)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    userId: int
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)
    embeddingProfile: str


class SearchResultItem(BaseModel):
    recordId: int
    similarity: float


class SearchResponse(BaseModel):
    results: list[SearchResultItem]

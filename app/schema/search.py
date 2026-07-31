"""개인 검색 API 요청·응답 스키마 (personal-search.md §1, §6)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    userId: int
    query: str = Field(min_length=1)
    # 공용 계약 08 §6.1 의 `size` 기본값과 같은 20. back 은 `sizeOrDefault()`로 항상
    # 명시해 보내므로(`RecordSearchService`) 이 기본값이 실경로에서 쓰이지는 않지만,
    # **드러나지 않았을 뿐 계약과 어긋나 있었다**(S15P11A705-213).
    limit: int = Field(default=20, ge=1, le=100)
    embeddingProfile: str


class SearchResultItem(BaseModel):
    recordId: int
    contextId: int
    similarity: float


class SearchResponse(BaseModel):
    results: list[SearchResultItem]

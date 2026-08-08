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
    # 재정렬(S15P11A705-339)이 이미 계산하는 키워드 매치 여부를 버리지 않고 싣는다
    # (S15P11A705-399). 결과를 보여줄지 정하는 데는 쓰지 않는다 — 이 필드는 신호를
    # 실어 보내는 것이고, 그 신호로 결과 유무를 정하는 것은 별도 게이트(S15P11A705-400)
    # 의 몫이다.
    keywordMatched: bool


class SearchResponse(BaseModel):
    results: list[SearchResultItem]

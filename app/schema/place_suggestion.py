"""Spring 중계용 이미지 장소 제안 내부 응답 계약."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class ExtractedPlace(ApiModel):
    place_name: str = Field(min_length=1, max_length=200)
    region_hints: list[str] = Field(default_factory=list, max_length=5)
    branch_hint: str | None = Field(default=None, max_length=100)
    evidence: list[str] = Field(default_factory=list, max_length=5)
    context_suggestion: str | None = Field(default=None, max_length=300)

    @field_validator("region_hints", "evidence")
    @classmethod
    def remove_blank_and_duplicates(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            key = normalized.casefold()
            if normalized and key not in seen:
                result.append(normalized)
                seen.add(key)
        return result

    @field_validator("branch_hint", "context_suggestion")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class PlaceExtractionResult(ApiModel):
    candidates: list[ExtractedPlace] = Field(default_factory=list, max_length=3)


class KakaoPlace(ApiModel):
    kakao_place_id: str
    name: str
    category_name: str | None = None
    address: str
    road_address: str | None = None
    phone: str | None = None
    place_url: str | None = None
    lat: float
    lng: float


class KakaoSearchStatus(StrEnum):
    SUCCESS = "SUCCESS"
    NO_RESULTS = "NO_RESULTS"
    FAILED = "FAILED"


class KakaoSearchResult(ApiModel):
    status: KakaoSearchStatus
    query: str
    items: list[KakaoPlace] = Field(default_factory=list, max_length=3)


class PlaceSuggestionCandidate(ApiModel):
    candidate_id: str
    extracted: ExtractedPlace
    kakao_search: KakaoSearchResult


class SuggestionWarningCode(StrEnum):
    NO_PLACE_CANDIDATES = "NO_PLACE_CANDIDATES"
    KAKAO_SEARCH_PARTIAL_FAILURE = "KAKAO_SEARCH_PARTIAL_FAILURE"
    KAKAO_PLACE_NOT_RECORDABLE = "KAKAO_PLACE_NOT_RECORDABLE"


class SuggestionWarning(ApiModel):
    code: SuggestionWarningCode
    message: str
    candidate_id: str | None = None


class PlaceSuggestionResponse(ApiModel):
    """FastAPI 내부 raw 응답. success/data envelope은 Spring이 한 번만 만든다."""

    request_id: str
    candidates: list[PlaceSuggestionCandidate] = Field(default_factory=list, max_length=3)
    warnings: list[SuggestionWarning] = Field(default_factory=list)

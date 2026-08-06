"""벤치마크와 같은 Gemini 내부 이미지 분석 결과."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class VisitStatus(StrEnum):
    WANT_TO_VISIT = "WANT_TO_VISIT"
    VISITED = "VISITED"
    UNKNOWN = "UNKNOWN"


class VisionPlaceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    place_name: str = Field(min_length=1)
    area_hint: str | None
    evidence: str | None
    visit_status: VisitStatus
    context_suggestion: str | None
    context_evidence: str | None
    confidence: float = Field(ge=0, le=1)


class VisionAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_text: str
    place_candidates: list[VisionPlaceCandidate] = Field(max_length=3)
    confidence: float = Field(ge=0, le=1)

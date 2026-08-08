"""검색 결과 LLM 관련도 재판정 요청·응답 스키마 (S15P11A705-337 후속, 4번째 검색 신호).

이 후보는 Preset 이 아니라 **검색 후보 기록**이다 — `contextId`·장소명·본문(`body`)을
담는다. `llm_client.py`(키워드 판정)의 후보 dict 와 모양이 다르므로 새 pydantic 모델을
둔다. Context 본문은 back(Spring)이 능동적으로 이 요청에 실어 보낸다 — back→ai 요청
방향은 "FastAPI 는 조회 응답에 본문을 싣지 않는다"는 기존 계약(05_AI_설계 §9.4)의
반대 방향이라 저촉되지 않는다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RelevanceLabel = Literal[
    "VERY_RELEVANT", "RELEVANT", "WEAKLY_RELEVANT", "NOT_RELEVANT"
]


class RelevanceCandidate(BaseModel):
    contextId: int
    placeName: str
    body: str


class RelevanceJudgeRequest(BaseModel):
    query: str = Field(min_length=1)
    candidates: list[RelevanceCandidate] = Field(min_length=1)


class RelevanceJudgment(BaseModel):
    contextId: int
    relevance: RelevanceLabel


class RelevanceJudgeResponse(BaseModel):
    results: list[RelevanceJudgment]

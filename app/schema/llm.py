"""LLM 판정 구조화 출력 (keyword-preset.md §4.2)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KeywordSelection:
    keyword_id: int
    confidence: float | None


@dataclass
class JudgeResult:
    selected: list[KeywordSelection] = field(default_factory=list)
    unmatched_concepts: list[str] = field(default_factory=list)

    # 실제로 답한 모델. 폴백 체인이 있으면 설정의 1순위와 다를 수 있고,
    # `ai.context_keyword_analysis.model_profile`에는 **답한 모델**이 들어가야 한다
    # (keyword-preset.md §5.2 "판정에 사용한 모델 식별 정보"). None이면 호출자가
    # 설정값으로 대신한다 — Fake 클라이언트가 그 경로다.
    model: str | None = None

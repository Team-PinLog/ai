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

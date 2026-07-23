"""Keyword Preset 메모리 캐시.

25~30개 준정적 데이터를 프로세스 메모리에 둔다(architecture.md §4). 기동 시 1회
적재하며, BLOCKED는 후보 집합에서 제외한다. 적재 0건이면 기동 실패로 처리한다.

한 요청은 캐시 스냅샷을 한 번 잡아 끝까지 들고 간다(keyword-preset.md §2). 재적재가
버전을 섞지 않도록, 후보 검색·저장은 아래 Snapshot 객체를 공유한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_BLOCKED = "BLOCKED"


def _to_array(value) -> np.ndarray:
    """pgvector 컬럼 값을 float32 ndarray로. asyncpg는 pgvector.Vector로 반환한다."""
    if hasattr(value, "to_numpy"):
        return value.to_numpy().astype(np.float32)
    return np.asarray(value, dtype=np.float32)


@dataclass(frozen=True)
class Preset:
    id: int
    code: str
    display_name: str
    category: str
    description: str
    examples: list[str]
    visibility: str
    version: int
    embedding: np.ndarray


@dataclass(frozen=True)
class PresetSnapshot:
    presets: tuple[Preset, ...]

    def as_dicts(self) -> list[dict]:
        """LLM 판정 입력 구성을 위한 dict 뷰(id/display_name/category/description/examples)."""
        return [
            {
                "id": p.id,
                "display_name": p.display_name,
                "category": p.category,
                "description": p.description,
                "examples": p.examples,
            }
            for p in self.presets
        ]


class PresetCache:
    def __init__(self) -> None:
        self._snapshot: PresetSnapshot | None = None

    def load(self, rows: list) -> int:
        """DB 행으로 캐시를 채운다. BLOCKED 제외. 적재 건수를 반환한다."""
        presets = [
            Preset(
                id=r["id"],
                code=r["code"],
                display_name=r["display_name"],
                category=r["category"],
                description=r["description"],
                examples=list(r["examples"]),
                visibility=r["visibility"],
                version=r["version"],
                embedding=_to_array(r["embedding"]),
            )
            for r in rows
            if r["visibility"] != _BLOCKED
        ]
        self._snapshot = PresetSnapshot(presets=tuple(presets))
        return len(presets)

    def snapshot(self) -> PresetSnapshot:
        if self._snapshot is None:
            raise RuntimeError("PresetCache not loaded")
        return self._snapshot

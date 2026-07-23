"""ai.context_keyword (delete-insert)와 ai.context_keyword_analysis (UPSERT).

Keyword 저장은 UPSERT가 아니라 delete-insert다. 재판정 결과가 이전보다 적을 수 있어
사라져야 할 이전 Keyword를 남기면 안 된다(keyword-preset.md §5). 삭제 범위는 언제나
지금 판정 중인 그 context_id 하나다.
"""
from __future__ import annotations

import json

import asyncpg

_DELETE = "DELETE FROM ai.context_keyword WHERE context_id = $1"

_INSERT = """
INSERT INTO ai.context_keyword (context_id, keyword_id, confidence, preset_version)
VALUES ($1, $2, $3, $4)
"""

_UPSERT_ANALYSIS = """
INSERT INTO ai.context_keyword_analysis
    (context_id, preset_version, unmatched_concepts, model_profile, updated_at)
VALUES ($1, $2, $3::jsonb, $4, now())
ON CONFLICT (context_id) DO UPDATE SET
    preset_version = EXCLUDED.preset_version,
    unmatched_concepts = EXCLUDED.unmatched_concepts,
    model_profile = EXCLUDED.model_profile,
    updated_at = now()
"""


async def replace(
    conn: asyncpg.Connection,
    context_id: int,
    selections: list[tuple[int, float | None]],
    preset_version: int,
) -> None:
    """기존 Keyword 전량 삭제 후 재삽입. selections는 (keyword_id, confidence). 0건 허용."""
    await conn.execute(_DELETE, context_id)
    if selections:
        await conn.executemany(
            _INSERT,
            [
                (context_id, kid, conf, preset_version)
                for kid, conf in selections
            ],
        )


async def upsert_analysis(
    conn: asyncpg.Connection,
    context_id: int,
    preset_version: int,
    unmatched_concepts: list[str],
    model_profile: str,
) -> None:
    """unmatchedConcepts 기록. 비어 있어도 행은 남긴다(분석 데이터)."""
    await conn.execute(
        _UPSERT_ANALYSIS,
        context_id,
        preset_version,
        json.dumps(unmatched_concepts, ensure_ascii=False),
        model_profile,
    )

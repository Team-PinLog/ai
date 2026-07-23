"""ai.keyword_preset 조회.

적재 범위는 is_active = true 이고 현재 Embedding Profile과 일치하는 행이다
(keyword-preset.md §2). 컬럼명은 마이그레이션 V100 기준(is_active).
"""
from __future__ import annotations

import asyncpg

_LOAD_ACTIVE = """
SELECT id, code, display_name, category, description, examples,
       visibility, version, embedding
FROM ai.keyword_preset
WHERE is_active = true
  AND embedding_profile = $1
"""


async def load_active(conn: asyncpg.Connection, embedding_profile: str) -> list:
    return await conn.fetch(_LOAD_ACTIVE, embedding_profile)

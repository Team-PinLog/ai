"""ai.context_embedding 접근.

개인 검색 Query와 처리 파이프라인의 UPSERT·조회를 담당한다.
is_deleted는 읽기만 하며 FastAPI가 변경하지 않는다(architecture.md §5.3).
UPSERT의 ON CONFLICT SET 절에 is_deleted를 넣지 않는다 — 삭제된 Context의 Embedding이
되살아나는 것을 막는다(context-processing.md §4.4).
"""
from __future__ import annotations

import asyncpg

# personal-search.md §4. 필터를 CTE로 분리해 벡터 연산이 필터 아래로 내려가지 않게 한다.
# 정확 cosine 검색(ANN 인덱스 없음). GROUP BY record_id + MAX(similarity)로 Record 단위 집계.
_SEARCH = """
WITH candidate AS (
    SELECT e.record_id,
           e.embedding
    FROM ai.context_embedding e
    JOIN ai.context_ai_state s
      ON s.context_id = e.context_id
    WHERE e.user_id = $1
      AND e.is_deleted = false
      AND e.embedding_profile = $2
      AND s.embedding_status = 'COMPLETED'
),
scored AS (
    SELECT record_id,
           1 - (embedding <=> $3) AS similarity
    FROM candidate
)
SELECT record_id,
       MAX(similarity) AS similarity
FROM scored
GROUP BY record_id
ORDER BY similarity DESC
LIMIT $4
"""


async def search(
    conn: asyncpg.Connection,
    user_id: int,
    embedding_profile: str,
    query_embedding: list[float],
    limit: int,
) -> list:
    return await conn.fetch(
        _SEARCH, user_id, embedding_profile, query_embedding, limit
    )


# is_deleted를 SET 절에서 의도적으로 제외한다(architecture.md §5.3).
_UPSERT = """
INSERT INTO ai.context_embedding
    (context_id, user_id, record_id, embedding, embedding_profile)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (context_id) DO UPDATE SET
    user_id = EXCLUDED.user_id,
    record_id = EXCLUDED.record_id,
    embedding = EXCLUDED.embedding,
    embedding_profile = EXCLUDED.embedding_profile,
    updated_at = now()
"""


async def upsert(
    conn: asyncpg.Connection,
    context_id: int,
    user_id: int,
    record_id: int,
    embedding: list[float],
    embedding_profile: str,
) -> None:
    await conn.execute(
        _UPSERT, context_id, user_id, record_id, embedding, embedding_profile
    )


async def load_vector(conn: asyncpg.Connection, context_id: int):
    """Keyword 단계에서 벡터를 보유하지 못한 경합 경로용 fallback 조회."""
    return await conn.fetchrow(
        "SELECT embedding, embedding_profile "
        "FROM ai.context_embedding WHERE context_id = $1",
        context_id,
    )

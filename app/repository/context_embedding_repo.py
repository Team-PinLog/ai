"""ai.context_embedding 접근.

E1은 개인 검색 Query만 담당한다. UPSERT(처리 파이프라인)는 E2에서 추가한다.
is_deleted는 읽기만 하며 FastAPI가 변경하지 않는다(architecture.md §5.3).
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

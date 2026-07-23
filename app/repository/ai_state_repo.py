"""ai.context_ai_state 조건부 전이.

repository는 rowcount만 반환하고 중단 여부는 service가 판단한다(state-machine.md §3.2).
컬럼명 조립 경로가 있으므로 Stage 열거형 값만 SQL로 들어간다 — 임의 문자열 차단.

FastAPI가 수행 가능한 전이(state-machine.md §2):
    PENDING → PROCESSING, PROCESSING → PROCESSING(만료 재선점),
    PROCESSING → COMPLETED, PROCESSING → FAILED
PENDING·CANCELLED·retry_count·is_deleted는 절대 쓰지 않는다.
"""
from __future__ import annotations

from datetime import timedelta
from enum import StrEnum

import asyncpg


class Stage(StrEnum):
    EMBEDDING = "embedding_status"
    KEYWORD = "keyword_status"


def _rowcount(status: str) -> int:
    # asyncpg execute()는 'UPDATE N' 형태의 명령 태그를 반환한다.
    return int(status.split()[-1])


async def precheck(conn: asyncpg.Connection, context_id: int):
    """사전 검사(잠금 없음). 불필요한 API 비용 차단용이며 정합성 보장이 아니다."""
    return await conn.fetchrow(
        "SELECT embedding_status, keyword_status "
        "FROM ai.context_ai_state WHERE context_id = $1",
        context_id,
    )


async def load_resume(conn: asyncpg.Connection, context_id: int):
    """부분 재개 판정용 조인 조회(partial-resume.md §2)."""
    return await conn.fetchrow(
        """
        SELECT e.embedding,
               e.embedding_profile AS emb_profile,
               s.embedding_status,
               s.keyword_status
        FROM ai.context_ai_state s
        LEFT JOIN ai.context_embedding e ON e.context_id = s.context_id
        WHERE s.context_id = $1
        """,
        context_id,
    )


async def lock_state(conn: asyncpg.Connection, context_id: int):
    """저장 트랜잭션 안에서 상태를 잠그고 재검사한다(SELECT ... FOR UPDATE)."""
    return await conn.fetchrow(
        "SELECT embedding_status, keyword_status "
        "FROM ai.context_ai_state WHERE context_id = $1 FOR UPDATE",
        context_id,
    )


async def try_start(
    conn: asyncpg.Connection,
    context_id: int,
    stage: Stage,
    processing_expiry_sec: int,
) -> int:
    """PENDING이거나 만료된 stale PROCESSING일 때만 PROCESSING으로 전이. rowcount 반환."""
    col = stage.value
    keyword_guard = (
        "AND embedding_status = 'COMPLETED'" if stage is Stage.KEYWORD else ""
    )
    sql = f"""
        UPDATE ai.context_ai_state
        SET {col} = 'PROCESSING',
            updated_at = now()
        WHERE context_id = $1
          AND {col} IN ('PENDING', 'PROCESSING')
          AND ({col} = 'PENDING' OR updated_at < now() - $2::interval)
          {keyword_guard}
    """
    status = await conn.execute(
        sql, context_id, timedelta(seconds=processing_expiry_sec)
    )
    return _rowcount(status)


async def complete(conn: asyncpg.Connection, context_id: int, stage: Stage) -> int:
    """PROCESSING → COMPLETED. 저장 트랜잭션 안에서 호출. WHERE 가드 유지."""
    col = stage.value
    keyword_guard = (
        "AND embedding_status = 'COMPLETED'" if stage is Stage.KEYWORD else ""
    )
    sql = f"""
        UPDATE ai.context_ai_state
        SET {col} = 'COMPLETED',
            updated_at = now()
        WHERE context_id = $1
          AND {col} = 'PROCESSING'
          {keyword_guard}
    """
    return _rowcount(await conn.execute(sql, context_id))


async def fail(conn: asyncpg.Connection, context_id: int, stage: Stage) -> int:
    """PROCESSING → FAILED (영구 오류). PROCESSING 가드로 CANCELLED를 덮지 않는다."""
    col = stage.value
    sql = f"""
        UPDATE ai.context_ai_state
        SET {col} = 'FAILED',
            updated_at = now()
        WHERE context_id = $1
          AND {col} = 'PROCESSING'
    """
    return _rowcount(await conn.execute(sql, context_id))

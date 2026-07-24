"""데이터 빌더. 테스트는 관심 있는 값만 지정한다.

embedding_profile·is_deleted·두 status는 항상 명시 가능하다 — 이 조합이 곧 테스트 대상이다.
**본문 버전 인자를 두지 않는다.** 그런 컬럼이 없으며, 헬퍼에 남기면 제거된 개념이 되살아난다.
Context 수정은 version=2가 아니라 context_id가 다른 두 State로 표현한다(계약 §4.2).
"""
from __future__ import annotations

from datetime import datetime

import asyncpg

from tests.fakes import deterministic_vector


async def make_preset(
    conn: asyncpg.Connection,
    *,
    id: int,
    code: str,
    embedding_profile: str,
    display_name: str = "표시",
    category: str = "COMPANION",
    description: str = "의미 범위",
    examples: list[str] | None = None,
    embedding: list[float] | None = None,
    visibility: str = "PUBLIC",
    is_active: bool = True,
    version: int = 1,
) -> None:
    await conn.execute(
        """
        INSERT INTO ai.keyword_preset
            (id, code, display_name, category, description, examples,
             embedding, embedding_profile, visibility, is_active, version)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        """,
        id, code, display_name, category, description,
        examples or ["예시 문장"],
        embedding if embedding is not None else deterministic_vector(code),
        embedding_profile, visibility, is_active, version,
    )


async def make_state(
    conn: asyncpg.Connection,
    *,
    context_id: int,
    embedding_status: str = "PENDING",
    keyword_status: str = "PENDING",
    retry_count: int = 0,
    updated_at: datetime | None = None,
) -> None:
    if updated_at is None:
        await conn.execute(
            "INSERT INTO ai.context_ai_state "
            "(context_id, embedding_status, keyword_status, retry_count) "
            "VALUES ($1,$2,$3,$4)",
            context_id, embedding_status, keyword_status, retry_count,
        )
    else:
        await conn.execute(
            "INSERT INTO ai.context_ai_state "
            "(context_id, embedding_status, keyword_status, retry_count, updated_at) "
            "VALUES ($1,$2,$3,$4,$5)",
            context_id, embedding_status, keyword_status, retry_count, updated_at,
        )


async def make_embedding(
    conn: asyncpg.Connection,
    *,
    context_id: int,
    user_id: int,
    record_id: int,
    embedding_profile: str,
    embedding: list[float] | None = None,
    text_for_vector: str | None = None,
    is_deleted: bool = False,
) -> None:
    if embedding is None:
        embedding = deterministic_vector(text_for_vector or f"ctx-{context_id}")
    await conn.execute(
        "INSERT INTO ai.context_embedding "
        "(context_id, user_id, record_id, embedding, embedding_profile, is_deleted) "
        "VALUES ($1,$2,$3,$4,$5,$6)",
        context_id, user_id, record_id, embedding, embedding_profile, is_deleted,
    )

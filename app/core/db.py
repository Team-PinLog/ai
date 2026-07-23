"""asyncpg 커넥션 풀과 세션 경계.

- 커넥션마다 search_path를 ai로 고정한다(architecture.md §5.3).
- pgvector 타입을 등록해 VECTOR 컬럼을 파이썬 list/ndarray로 바인딩·수신한다.
- 기동 시 DDL을 실행하지 않는다. 테이블이 없으면 기동 실패로 드러난다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg
from pgvector.asyncpg import register_vector


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=10,
            init=self._init_connection,
        )

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        await conn.execute("SET search_path = ai")
        await register_vector(conn)

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """단일 트랜잭션 세션. TX3(FOR UPDATE → 저장 → 전이)에 사용."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield conn

"""asyncpg 커넥션 풀과 세션 경계.

- 커넥션마다 search_path를 ai, public으로 고정한다(public=vector 확장 소재,
  core는 경로 밖 유지 — architecture.md §6.3, T21).
- pgvector 타입을 등록해 VECTOR 컬럼을 파이썬 list/ndarray로 바인딩·수신한다.
- 기동 시 DDL을 실행하지 않는다. 테이블이 없으면 기동 실패로 드러난다.
- **DB 실패의 오류 분류가 붙는 지점이다**(`db_errors.py`, failure-recovery.md §2.5).

분류를 repository 함수가 아니라 여기 세션 경계에 두는 이유는, 이 티켓이 겨냥한
**"DB 연결 실패"가 질의가 아니라 커넥션 획득에서 나기 때문**이다. repository 함수에
데코레이터를 걸면 `pool.acquire()`가 그 밖이라 접속 실패를 놓친다. 여기에 두면 획득과
질의가 한 번에 덮이고, 저장소를 새로 만들어도 분류가 자동으로 따라온다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg
from pgvector.asyncpg import register_vector

from app.core.db_errors import translate_db_errors


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
        # ai 우선 + public(vector 확장 소재). public을 빼면 VECTOR 타입 해석과
        # register_vector가 실패한다. core는 경로에 없어 실수 참조는 여전히 차단된다.
        await conn.execute("SET search_path = ai, public")
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

    async def _acquire(self) -> asyncpg.Connection:
        """커넥션 획득. 여기서만 `OSError`를 DB 실패로 읽는다(`db_errors.py` 참조).

        `async with self.pool.acquire()` 대신 획득과 반납을 갈라 놓은 것은 **획득 단계와
        사용 단계의 번역 규칙이 다르기 때문**이다. 반납은 호출부의 `finally`가 한다.
        """
        with translate_db_errors(connecting=True):
            return await self.pool.acquire()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        conn = await self._acquire()
        try:
            # 사용 단계는 asyncpg 예외만 번역한다. 이 블록에는 호출부의 코드가 함께
            # 들어오므로 `OSError`까지 잡으면 DB와 무관한 실패를 503으로 오분류한다.
            with translate_db_errors():
                yield conn
        finally:
            await self.pool.release(conn)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """단일 트랜잭션 세션. TX3(FOR UPDATE → 저장 → 전이)에 사용."""
        conn = await self._acquire()
        try:
            with translate_db_errors():
                async with conn.transaction():
                    yield conn
        finally:
            await self.pool.release(conn)

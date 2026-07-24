"""통합 테스트 픽스처.

- 실제 PostgreSQL + pgvector(Testcontainers). SQLite·H2로 대체하지 않는다 — 검증 대상이
  조건부 UPDATE 영향 행 수·FOR UPDATE·<=> 연산자·ON CONFLICT SET 절이라 전부 방언 의존적.
- 격리는 TRUNCATE. 동시성 테스트가 여러 커넥션을 쓰므로 트랜잭션 롤백 격리를 못 쓴다.
- Profile 문자열 리터럴을 테스트에 두지 않는다 — settings fixture 경유(model-profile.md §2.1).
"""
from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from pgvector.asyncpg import register_vector
from testcontainers.postgres import PostgresContainer

from app.core.config import Settings, get_settings
from app.core.db import Database

# back compose.yaml·Testcontainers와 일치 유지(재현성). 롤링 태그 금지.
PGVECTOR_IMAGE = "pgvector/pgvector:0.8.1-pg16"

_SCHEMA_SQL = (Path(__file__).parent / "schema" / "ai_snapshot.sql").read_text(encoding="utf-8")

_AI_TABLES = [
    "ai.context_keyword_analysis",
    "ai.context_keyword",
    "ai.context_embedding",
    "ai.context_ai_state",
    "ai.keyword_preset",
]

# 테스트 설정값. Profile은 여기 한 곳에서만 정의하고 각 테스트는 settings fixture로 받는다.
_TEST_ENV = {
    "GMS_API_KEY": "test-key",
    "GMS_BASE_URL": "https://gms.example/gmsapi/api.openai.com/v1",
    "PINLOG_EMBEDDING_MODEL": "text-embedding-3-small",
    "PINLOG_EMBEDDING_DIMENSION": "1536",
    "PINLOG_EMBEDDING_DISTANCE": "cosine",
    "PINLOG_EMBEDDING_PROFILE": "openai-text-embedding-3-small-1536-cosine-v1",
    "PINLOG_JUDGE_MODEL": "gemini-2.5-flash",
    "KEYWORD_CANDIDATE_TOP_K": "10",
    "SIMILARITY_FLOOR": "0.30",
    "PROCESSING_EXPIRY_SEC": "600",
    "INTERNAL_SHARED_SECRET": "test-secret",
}

# app.main 로드 시 create_app()이 get_settings()를 호출하므로, .env가 없는 CI에서도
# import가 성공하도록 placeholder를 미리 넣는다. 실제 DATABASE_URL은 settings fixture가
# 컨테이너 dsn으로 덮는다(cache_clear 후).
for _k, _v in {**_TEST_ENV, "DATABASE_URL": "postgresql://placeholder/db"}.items():
    os.environ.setdefault(_k, _v)


@pytest.fixture(scope="session")
def _pg_container():
    with PostgresContainer(PGVECTOR_IMAGE, driver=None) as pg:
        yield pg


@pytest.fixture(scope="session")
def dsn(_pg_container) -> str:
    # asyncpg용 DSN (testcontainers는 SQLAlchemy 스타일 URL을 주므로 재구성)
    return (
        f"postgresql://{_pg_container.username}:{_pg_container.password}"
        f"@{_pg_container.get_container_host_ip()}"
        f":{_pg_container.get_exposed_port(5432)}/{_pg_container.dbname}"
    )


@pytest.fixture(scope="session")
def settings(dsn) -> Settings:
    for key, value in {**_TEST_ENV, "DATABASE_URL": dsn}.items():
        os.environ[key] = value
    # app.main 모듈 로드 시 create_app()이 .env로 get_settings()를 이미 캐시했을 수 있다.
    # 테스트 env로 재설정해 미들웨어(create_app 내부)와 fixture가 같은 설정을 보게 한다.
    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture(scope="session")
async def _schema(dsn):
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(_SCHEMA_SQL)
    finally:
        await conn.close()
    yield


@pytest_asyncio.fixture
async def db(settings, _schema) -> Database:
    database = Database(settings.database_url)
    await database.connect()
    # 함수별 격리: 모든 ai 테이블 TRUNCATE
    async with database.acquire() as conn:
        await conn.execute(f"TRUNCATE {', '.join(_AI_TABLES)} RESTART IDENTITY CASCADE")
    try:
        yield database
    finally:
        await database.disconnect()


@pytest_asyncio.fixture
async def conn(db) -> asyncpg.Connection:
    """단일 커넥션(register_vector 적용). 저장소·빌더 테스트용."""
    async with db.acquire() as connection:
        yield connection


async def raw_connect(dsn: str) -> asyncpg.Connection:
    """동시성 테스트에서 별도 커넥션이 필요할 때 사용(pgvector 등록 포함)."""
    connection = await asyncpg.connect(dsn)
    await connection.execute("SET search_path = ai, public")
    await register_vector(connection)
    return connection

"""저장소 계층 — 실제 PG+pgvector. 조건부 UPDATE rowcount·UPSERT SET·delete-insert·검색."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.repository import ai_state_repo, context_embedding_repo, context_keyword_repo
from app.repository.ai_state_repo import Stage
from tests.builders import make_embedding, make_preset, make_state
from tests.fakes import deterministic_vector

EXPIRY = 600


# ── try_start ──────────────────────────────────────────
async def test_try_start_pending_transitions(conn):
    await make_state(conn, context_id=1, embedding_status="PENDING")
    assert await ai_state_repo.try_start(conn, 1, Stage.EMBEDDING, EXPIRY) == 1


async def test_try_start_cancelled_does_not_transition(conn):
    await make_state(conn, context_id=1, embedding_status="CANCELLED")
    assert await ai_state_repo.try_start(conn, 1, Stage.EMBEDDING, EXPIRY) == 0


async def test_try_start_reclaims_expired_processing(conn):
    old = datetime.now(timezone.utc) - timedelta(minutes=11)
    await make_state(conn, context_id=1, embedding_status="PROCESSING", updated_at=old)
    assert await ai_state_repo.try_start(conn, 1, Stage.EMBEDDING, EXPIRY) == 1


async def test_try_start_skips_fresh_processing(conn):
    await make_state(conn, context_id=1, embedding_status="PROCESSING")  # updated_at=now
    assert await ai_state_repo.try_start(conn, 1, Stage.EMBEDDING, EXPIRY) == 0


async def test_try_start_keyword_requires_embedding_completed(conn):
    await make_state(conn, context_id=1, embedding_status="PENDING", keyword_status="PENDING")
    assert await ai_state_repo.try_start(conn, 1, Stage.KEYWORD, EXPIRY) == 0
    await make_state(conn, context_id=2, embedding_status="COMPLETED", keyword_status="PENDING")
    assert await ai_state_repo.try_start(conn, 2, Stage.KEYWORD, EXPIRY) == 1


# ── complete / fail ────────────────────────────────────
async def test_complete_embedding(conn):
    await make_state(conn, context_id=1, embedding_status="PROCESSING")
    assert await ai_state_repo.complete(conn, 1, Stage.EMBEDDING) == 1


async def test_fail_guarded_by_processing(conn):
    await make_state(conn, context_id=1, embedding_status="PROCESSING")
    assert await ai_state_repo.fail(conn, 1, Stage.EMBEDDING) == 1
    await make_state(conn, context_id=2, embedding_status="CANCELLED")
    # CANCELLED는 PROCESSING 가드에 걸려 FAILED로 덮이지 않음
    assert await ai_state_repo.fail(conn, 2, Stage.EMBEDDING) == 0


# ── UPSERT: is_deleted 제외 회귀 ────────────────────────
async def test_upsert_does_not_revive_deleted(conn, settings):
    profile = settings.embedding_profile
    await make_embedding(conn, context_id=1, user_id=7, record_id=3,
                         embedding_profile=profile, is_deleted=True)
    await context_embedding_repo.upsert(
        conn, 1, 7, 3, deterministic_vector("new"), profile
    )
    row = await conn.fetchrow("SELECT is_deleted FROM ai.context_embedding WHERE context_id=1")
    assert row["is_deleted"] is True  # UPSERT SET 절에 is_deleted가 없어야 유지됨


# ── delete-insert ──────────────────────────────────────
async def test_keyword_replace_is_delete_insert(conn, settings):
    await make_preset(conn, id=101, code="A", embedding_profile=settings.embedding_profile)
    await make_preset(conn, id=102, code="B", embedding_profile=settings.embedding_profile)
    await context_keyword_repo.replace(conn, 1, [(101, 0.9), (102, 0.8)], preset_version=1)
    assert await conn.fetchval("SELECT count(*) FROM ai.context_keyword WHERE context_id=1") == 2
    # 재판정 결과가 더 적음 → 이전 것이 남지 않아야
    await context_keyword_repo.replace(conn, 1, [(101, 0.5)], preset_version=1)
    rows = await conn.fetch("SELECT keyword_id FROM ai.context_keyword WHERE context_id=1")
    assert [r["keyword_id"] for r in rows] == [101]


async def test_keyword_replace_zero_rows(conn):
    await context_keyword_repo.replace(conn, 1, [], preset_version=1)
    assert await conn.fetchval("SELECT count(*) FROM ai.context_keyword WHERE context_id=1") == 0


# ── search: DISTINCT ON 대표 contextId ──────────────────
async def test_search_returns_representative_context_per_record(conn, settings):
    profile = settings.embedding_profile
    q = deterministic_vector("query")
    # record 40: context 300(질의와 동일 벡터=최고), 301(무관)
    await make_state(conn, context_id=300, embedding_status="COMPLETED", keyword_status="COMPLETED")
    await make_embedding(conn, context_id=300, user_id=1, record_id=40,
                         embedding_profile=profile, embedding=q)
    await make_state(conn, context_id=301, embedding_status="COMPLETED", keyword_status="COMPLETED")
    await make_embedding(conn, context_id=301, user_id=1, record_id=40,
                         embedding_profile=profile, embedding=deterministic_vector("무관"))
    rows = await context_embedding_repo.search(conn, 1, profile, q, 10)
    assert len(rows) == 1  # record 40 한 번만
    assert rows[0]["record_id"] == 40
    assert rows[0]["context_id"] == 300  # 최고 유사도 대표


async def test_search_scoped_to_user(conn, settings):
    profile = settings.embedding_profile
    q = deterministic_vector("q")
    for cid, uid, rid in [(1, 10, 100), (2, 20, 200)]:
        await make_state(conn, context_id=cid, embedding_status="COMPLETED")
        await make_embedding(conn, context_id=cid, user_id=uid, record_id=rid,
                             embedding_profile=profile, embedding=q)
    rows = await context_embedding_repo.search(conn, 10, profile, q, 100)  # limit 넉넉히
    assert [r["record_id"] for r in rows] == [100]  # 타 유저(200) 제외

"""파이프라인 계층 — 계약 §16 시나리오(integration-tests.md §3 정본).

Fake만 사용(실 GMS 없음), 호출 횟수 단언이 핵심, 동시성은 on_call 훅으로 순서 고정(sleep 금지),
저장 불변식은 FOR UPDATE 재검사(CANCELLED면 rowcount 0 폐기). 서비스는 생성자 주입이라
Fake로 직접 조립한다. 21번(AI 미완 Collection)은 BE 소관이라 제외.
"""
from __future__ import annotations

import asyncio

from app.cache.preset_cache import PresetCache
from app.repository import ai_state_repo, context_embedding_repo, keyword_preset_repo
from app.repository.ai_state_repo import Stage
from app.schema.context import ContextProcessRequest
from app.service.context_processing import ContextProcessingService
from app.service.embedding_service import EmbeddingService
from app.service.keyword_service import KeywordService
from tests.builders import make_embedding, make_preset, make_state
from tests.conftest import raw_connect
from tests.fakes import FakeEmbeddingClient, FakeLLMClient, deterministic_vector


# ── 헬퍼 ────────────────────────────────────────────────
def _services(db, settings, *, emb=None, llm=None, cache=None):
    emb = emb or FakeEmbeddingClient()
    llm = llm or FakeLLMClient()
    if cache is None:
        cache = PresetCache()
        cache.load([])
    proc = ContextProcessingService(
        db,
        EmbeddingService(db, emb, settings),
        KeywordService(db, llm, cache, settings),
    )
    return proc, emb, llm


def _req(context_id, *, user_id=1, record_id=1, text="친구랑 저녁"):
    return ContextProcessRequest(
        contextId=context_id, userId=user_id, recordId=record_id, text=text
    )


async def _load_cache(conn, settings, specs):
    """specs: [{'id','code','visibility'?,'vec'?}]. vec는 후보 매칭용(context 텍스트와 동일)."""
    for s in specs:
        await make_preset(
            conn, id=s["id"], code=s["code"],
            embedding_profile=settings.embedding_profile,
            visibility=s.get("visibility", "PUBLIC"),
            embedding=deterministic_vector(s.get("vec", s["code"])),
        )
    rows = await keyword_preset_repo.load_active(conn, settings.embedding_profile)
    cache = PresetCache()
    cache.load(rows)
    return cache


def _cancel_hook(dsn, context_id):
    """모델 호출 시점에 다른 커넥션으로 삭제(CANCELLED + is_deleted)를 주입한다."""
    async def hook():
        c = await raw_connect(dsn)
        try:
            await c.execute(
                "UPDATE ai.context_ai_state SET embedding_status='CANCELLED', "
                "keyword_status='CANCELLED' WHERE context_id=$1", context_id,
            )
            await c.execute(
                "UPDATE ai.context_embedding SET is_deleted=true WHERE context_id=$1",
                context_id,
            )
        finally:
            await c.close()
    return hook


async def _state(conn, cid):
    return await conn.fetchrow(
        "SELECT embedding_status, keyword_status, retry_count "
        "FROM ai.context_ai_state WHERE context_id=$1", cid
    )


async def _emb_count(conn, cid):
    return await conn.fetchval(
        "SELECT count(*) FROM ai.context_embedding WHERE context_id=$1", cid
    )


async def _kw_count(conn, cid):
    return await conn.fetchval(
        "SELECT count(*) FROM ai.context_keyword WHERE context_id=$1", cid
    )


# ── 취소 거부 ───────────────────────────────────────────
async def test_cancelled_rejects_late_result(db, conn, settings):
    # 시나리오 2: 수정 후 구 Context 결과 도착 → 저장 거부
    await make_state(conn, context_id=1, embedding_status="CANCELLED", keyword_status="CANCELLED")
    await make_embedding(conn, context_id=1, user_id=1, record_id=1,
                         embedding_profile=settings.embedding_profile, is_deleted=True)
    proc, emb, llm = _services(db, settings)
    await proc.process(_req(1))
    row = await conn.fetchrow("SELECT is_deleted FROM ai.context_embedding WHERE context_id=1")
    assert row["is_deleted"] is True                    # false로 되돌아가지 않음
    st = await _state(conn, 1)
    assert st["embedding_status"] == "CANCELLED"        # COMPLETED로 바뀌지 않음
    assert emb.call_count == 0 and llm.call_count == 0  # 시작조차 안 함


async def test_cancelled_rejects_embedding_persist(db, conn, settings, dsn):
    # 시나리오 10: 모델 호출과 저장 사이 CANCELLED → embedding 저장 거부
    await make_state(conn, context_id=1, embedding_status="PENDING", keyword_status="PENDING")
    emb = FakeEmbeddingClient(on_call=_cancel_hook(dsn, 1))
    proc, emb, _ = _services(db, settings, emb=emb)
    await proc.process(_req(1))
    assert await _emb_count(conn, 1) == 0               # 결과 미저장
    st = await _state(conn, 1)
    assert st["embedding_status"] == "CANCELLED"        # FAILED로 덮이지 않음
    assert emb.call_count == 1                          # 정확히 한 번 호출 후 폐기


async def test_cancelled_rejects_keyword_persist(db, conn, settings, dsn):
    # 시나리오 11: keyword 저장 직전 CANCELLED → 저장 거부
    await make_state(conn, context_id=1, embedding_status="PENDING", keyword_status="PENDING")
    cache = await _load_cache(conn, settings, [{"id": 101, "code": "F", "vec": "친구랑 저녁"}])
    llm = FakeLLMClient(selected=[(101, 0.9)], on_call=_cancel_hook(dsn, 1))
    proc, emb, llm = _services(db, settings, llm=llm, cache=cache)
    await proc.process(_req(1))
    assert await _kw_count(conn, 1) == 0                # keyword 미저장
    st = await _state(conn, 1)
    assert st["keyword_status"] == "CANCELLED"
    assert emb.call_count == 1 and llm.call_count == 1


async def test_cancelled_blocks_late_embedding_insert(db, conn, settings, dsn):
    # 시나리오 12: embedding 행이 없는 상태에서 CANCELLED → 늦은 INSERT 차단
    await make_state(conn, context_id=1, embedding_status="PENDING", keyword_status="PENDING")

    async def hook():  # embedding 행이 없으므로 State만 CANCELLED로
        c = await raw_connect(dsn)
        try:
            await c.execute("UPDATE ai.context_ai_state SET embedding_status='CANCELLED', "
                            "keyword_status='CANCELLED' WHERE context_id=1")
        finally:
            await c.close()

    emb = FakeEmbeddingClient(on_call=hook)
    proc, emb, _ = _services(db, settings, emb=emb)
    await proc.process(_req(1))
    assert await _emb_count(conn, 1) == 0               # 행이 생기지 않음(status 검사가 유일 방어)


# ── 검색 경계 ───────────────────────────────────────────
async def test_search_excludes_cancelled_and_deleted(db, conn, settings):
    # 시나리오 3: is_deleted·embedding_status 두 필터가 각각 필요
    p = settings.embedding_profile
    q = deterministic_vector("질의")
    # A: is_deleted=true·COMPLETED → is_deleted 필터로 제외
    await make_state(conn, context_id=1, embedding_status="COMPLETED")
    await make_embedding(conn, context_id=1, user_id=9, record_id=10,
                         embedding_profile=p, embedding=q, is_deleted=True)
    # B: is_deleted=false·CANCELLED → embedding_status 필터로 제외
    await make_state(conn, context_id=2, embedding_status="CANCELLED")
    await make_embedding(conn, context_id=2, user_id=9, record_id=11,
                         embedding_profile=p, embedding=q, is_deleted=False)
    # C: 정상 → 포함
    await make_state(conn, context_id=3, embedding_status="COMPLETED")
    await make_embedding(conn, context_id=3, user_id=9, record_id=12,
                         embedding_profile=p, embedding=q, is_deleted=False)
    rows = await context_embedding_repo.search(conn, 9, p, q, 100)
    assert [r["record_id"] for r in rows] == [12]      # C만(A·B 각각 다른 조건으로 제외)


async def test_search_scoped_to_user_id(db, conn, settings):
    # 시나리오 19: user 범위 필터. limit을 크게 잡아 "우연히 안 나온 것" 아님을 보장
    p = settings.embedding_profile
    q = deterministic_vector("q")
    for cid, uid, rid in [(1, 100, 1000), (2, 200, 2000)]:
        await make_state(conn, context_id=cid, embedding_status="COMPLETED")
        await make_embedding(conn, context_id=cid, user_id=uid, record_id=rid,
                             embedding_profile=p, embedding=q)
    rows = await context_embedding_repo.search(conn, 100, p, q, 1000)
    assert [r["record_id"] for r in rows] == [1000]    # 타 유저(200) 제외


async def test_search_dedupes_by_record_with_max_similarity(db, conn, settings):
    # 시나리오 20: 한 record의 여러 Context → record 1회, 대표는 최고 유사도 Context
    p = settings.embedding_profile
    q = deterministic_vector("q")
    # ctx 1: 질의와 동일(최고), 2·3: 무관(낮음), 전부 record 50
    await make_state(conn, context_id=1, embedding_status="COMPLETED")
    await make_embedding(conn, context_id=1, user_id=7, record_id=50,
                         embedding_profile=p, embedding=q)
    for cid, txt in [(2, "무관A"), (3, "무관B")]:
        await make_state(conn, context_id=cid, embedding_status="COMPLETED")
        await make_embedding(conn, context_id=cid, user_id=7, record_id=50,
                             embedding_profile=p, embedding=deterministic_vector(txt))
    rows = await context_embedding_repo.search(conn, 7, p, q, 100)
    assert len(rows) == 1 and rows[0]["record_id"] == 50
    assert rows[0]["context_id"] == 1                  # 최고 유사도 대표
    assert rows[0]["similarity"] > 0.99                # 동일 벡터 ≈ 1(최댓값)


# ── Keyword ─────────────────────────────────────────────
async def test_keyword_query_excludes_cancelled_context(db, conn, settings):
    # 시나리오 4: 구 Context keyword 행은 물리적으로 남아도 State 조인 조회에서 제외
    await _load_cache(conn, settings, [{"id": 101, "code": "A"}])
    await make_state(conn, context_id=1, embedding_status="COMPLETED", keyword_status="COMPLETED")
    await conn.executemany(
        "INSERT INTO ai.context_keyword (context_id, keyword_id, confidence, preset_version) "
        "VALUES ($1,$2,$3,$4)", [(1, 101, 0.9, 1)],
    )
    # 삭제/수정으로 CANCELLED 전환
    await conn.execute("UPDATE ai.context_ai_state SET embedding_status='CANCELLED', "
                       "keyword_status='CANCELLED' WHERE context_id=1")
    # Spring의 조회 패턴(State 조인 keyword_status=COMPLETED)
    visible = await conn.fetch(
        "SELECT ck.keyword_id FROM ai.context_keyword ck "
        "JOIN ai.context_ai_state s ON s.context_id = ck.context_id "
        "WHERE ck.context_id=1 AND s.keyword_status='COMPLETED'"
    )
    assert visible == []                               # 조회에서 제외
    assert await _kw_count(conn, 1) == 1               # 행 자체는 물리적으로 남음


async def test_profile_mismatch_aborts_keyword_stage(db, conn, settings):
    # 시나리오 13: Context Embedding Profile ≠ 서버 Profile → 판정 중단(COMPLETED 아님)
    cache = await _load_cache(conn, settings, [{"id": 101, "code": "F", "vec": "친구랑 저녁"}])
    await make_state(conn, context_id=1, embedding_status="COMPLETED", keyword_status="PENDING")
    await make_embedding(conn, context_id=1, user_id=1, record_id=1,
                         embedding_profile="other-profile-v9",
                         embedding=deterministic_vector("친구랑 저녁"))
    proc, emb, llm = _services(db, settings, cache=cache)
    await proc.process(_req(1))
    assert llm.call_count == 0                          # LLM 미호출
    assert await _kw_count(conn, 1) == 0
    st = await _state(conn, 1)
    assert st["keyword_status"] == "FAILED"             # 판정 불가 ≠ 결과 0개(COMPLETED 아님)


async def test_empty_keyword_result_is_completed(db, conn, settings):
    # 시나리오 14: 매칭 없음 → 정상 COMPLETED, analysis 행 존재
    cache = await _load_cache(conn, settings, [{"id": 101, "code": "F", "vec": "친구랑 저녁"}])
    await make_state(conn, context_id=1, embedding_status="PENDING", keyword_status="PENDING")
    llm = FakeLLMClient(selected=[], unmatched=["미매칭 개념"])
    proc, emb, llm = _services(db, settings, llm=llm, cache=cache)
    await proc.process(_req(1))
    st = await _state(conn, 1)
    assert st["keyword_status"] == "COMPLETED"          # 0개도 정상 완료
    assert await _kw_count(conn, 1) == 0
    assert await conn.fetchval(
        "SELECT count(*) FROM ai.context_keyword_analysis WHERE context_id=1") == 1


async def test_blocked_preset_not_in_candidates(db, conn, settings):
    # 시나리오 15: BLOCKED Preset은 후보 집합에서 제외(캐시 적재 시)
    cache = await _load_cache(conn, settings, [
        {"id": 101, "code": "PUB", "visibility": "PUBLIC"},
        {"id": 999, "code": "BLK", "visibility": "BLOCKED"},
    ])
    ids = [p.id for p in cache.snapshot().presets]
    assert 999 not in ids and 101 in ids               # BLOCKED 제외, PUBLIC 유지


# ── 재개/상태 ───────────────────────────────────────────
async def test_partial_resume_skips_embedding_call(db, conn, settings):
    # 시나리오 7: Embedding 성공·Keyword 실패 → 재개 시 Embedding 재호출 0, Keyword만
    cache = await _load_cache(conn, settings, [{"id": 101, "code": "F", "vec": "친구랑 저녁"}])
    await make_state(conn, context_id=1, embedding_status="COMPLETED", keyword_status="PENDING")
    await make_embedding(conn, context_id=1, user_id=1, record_id=1,
                         embedding_profile=settings.embedding_profile,
                         embedding=deterministic_vector("친구랑 저녁"))
    llm = FakeLLMClient(selected=[(101, 0.8)])
    proc, emb, llm = _services(db, settings, llm=llm, cache=cache)
    await proc.process(_req(1))
    assert emb.call_count == 0                          # Embedding 재호출 없음
    assert llm.call_count == 1
    st = await _state(conn, 1)
    assert st["keyword_status"] == "COMPLETED"


async def test_stale_processing_is_resumable(db, conn, settings):
    # 시나리오 8: 만료된 PROCESSING은 재선점, 방금 PROCESSING은 재선점 안 됨(짝)
    from datetime import datetime, timedelta, timezone
    cache = await _load_cache(conn, settings, [{"id": 101, "code": "F", "vec": "친구랑 저녁"}])
    old = datetime.now(timezone.utc) - timedelta(minutes=11)
    # (a) 만료 → 재처리 완료
    await make_state(conn, context_id=1, embedding_status="PROCESSING",
                     keyword_status="PENDING", updated_at=old)
    llm = FakeLLMClient(selected=[(101, 0.7)])
    proc_a, emb_a, _ = _services(db, settings, llm=llm, cache=cache)
    await proc_a.process(_req(1))
    st = await _state(conn, 1)
    assert emb_a.call_count == 1 and st["embedding_status"] == "COMPLETED"
    # (b) 방금 → 재선점 안 됨
    await make_state(conn, context_id=2, embedding_status="PROCESSING", keyword_status="PENDING")
    proc_b, emb_b, _ = _services(db, settings, cache=cache)
    await proc_b.process(_req(2))
    st2 = await _state(conn, 2)
    assert emb_b.call_count == 0 and st2["embedding_status"] == "PROCESSING"


async def test_fastapi_never_writes_finalizer_failed(db, conn, settings):
    # 시나리오 16: retry 소진 stale. FastAPI는 retry·재시도소진 FAILED 미기록, FAILED 재개 안 함
    await make_state(conn, context_id=1, embedding_status="FAILED",
                     keyword_status="PENDING", retry_count=3)
    proc, emb, llm = _services(db, settings)
    await proc.process(_req(1))
    st = await _state(conn, 1)
    assert emb.call_count == 0 and llm.call_count == 0  # FAILED→PROCESSING 전이 없음
    assert st["retry_count"] == 3                       # retry_count 불변(읽지도 쓰지도 않음)
    assert st["embedding_status"] == "FAILED"


async def test_failed_transition_does_not_overwrite_cancelled(db, conn, settings):
    # 시나리오 17: CANCELLED 상태에 FastAPI FAILED 전이 → PROCESSING 가드로 rowcount 0
    await make_state(conn, context_id=1, embedding_status="CANCELLED", keyword_status="CANCELLED")
    affected = await ai_state_repo.fail(conn, 1, Stage.EMBEDDING)
    assert affected == 0                               # CANCELLED를 FAILED로 덮지 않음
    st = await _state(conn, 1)
    assert st["embedding_status"] == "CANCELLED"


# ── 계약위반/경합 ───────────────────────────────────────
async def test_context_edit_isolates_old_and_new_context(db, conn, settings):
    # 시나리오 1: 구 CANCELLED·신 PENDING. 구 저장 거부, 신 정상(구 Embedding 재사용 안 함)
    p = settings.embedding_profile
    cache = await _load_cache(conn, settings, [{"id": 101, "code": "F", "vec": "친구랑 저녁"}])
    # 구 Context: CANCELLED + is_deleted, Embedding 존재
    await make_state(conn, context_id=1, embedding_status="CANCELLED", keyword_status="CANCELLED")
    await make_embedding(conn, context_id=1, user_id=1, record_id=1,
                         embedding_profile=p, is_deleted=True, text_for_vector="구 본문")
    # 신 Context: 새 context_id, PENDING, Embedding 없음
    await make_state(conn, context_id=2, embedding_status="PENDING", keyword_status="PENDING")
    llm = FakeLLMClient(selected=[(101, 0.8)])
    proc, emb, llm = _services(db, settings, llm=llm, cache=cache)
    await proc.process(_req(1))                         # 구 → 거부
    await proc.process(_req(2, text="친구랑 저녁"))       # 신 → 처리
    assert emb.call_count == 1                          # 구는 미호출, 신만 1회(재사용 아님)
    assert await _emb_count(conn, 2) == 1               # 신 Embedding 생성
    old = await conn.fetchrow("SELECT is_deleted FROM ai.context_embedding WHERE context_id=1")
    assert old["is_deleted"] is True                   # 구는 불변
    assert (await _state(conn, 2))["keyword_status"] == "COMPLETED"


async def test_same_context_id_different_text_is_contract_violation(db, conn, settings):
    # 시나리오 5: 이미 COMPLETED인데 다른 text 요청 → 아무것도 바뀌지 않음(재생성·재판정 안 함)
    await make_state(conn, context_id=1, embedding_status="COMPLETED", keyword_status="COMPLETED")
    await make_embedding(conn, context_id=1, user_id=1, record_id=1,
                         embedding_profile=settings.embedding_profile, text_for_vector="원 본문")
    proc, emb, llm = _services(db, settings)
    await proc.process(_req(1, text="완전히 다른 본문"))  # 예외로 죽지 않아야
    assert emb.call_count == 0 and llm.call_count == 0  # 재생성·재판정 없음
    st = await _state(conn, 1)
    assert st["embedding_status"] == "COMPLETED" and st["keyword_status"] == "COMPLETED"


async def test_process_request_on_cancelled_does_not_start(db, conn, settings):
    # 시나리오 6·18: CANCELLED State에 처리 요청 → 시작조차 안 함(공유 단언)
    await make_state(conn, context_id=1, embedding_status="CANCELLED", keyword_status="CANCELLED")
    proc, emb, llm = _services(db, settings)
    await proc.process(_req(1))
    assert emb.call_count == 0 and llm.call_count == 0
    assert await _emb_count(conn, 1) == 0 and await _kw_count(conn, 1) == 0


async def test_concurrent_process_requests_single_effect(db, conn, settings):
    # 시나리오 9: 같은 contextId 동시 요청 2개 → 결과 중복 저장 없음
    cache = await _load_cache(conn, settings, [{"id": 101, "code": "F", "vec": "친구랑 저녁"}])
    await make_state(conn, context_id=1, embedding_status="PENDING", keyword_status="PENDING")
    llm = FakeLLMClient(selected=[(101, 0.9)])
    proc, emb, llm = _services(db, settings, llm=llm, cache=cache)
    await asyncio.gather(proc.process(_req(1)), proc.process(_req(1)))
    assert emb.call_count == 1                          # 조건부 UPDATE가 중복 실행 흡수
    assert await _emb_count(conn, 1) == 1               # Embedding 1행
    assert await _kw_count(conn, 1) == 1               # 판정 결과 개수와 일치(2배 아님)

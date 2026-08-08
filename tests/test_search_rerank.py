"""검색 keyword 재정렬 — on/off 후보 불변·강등·플래그 off 계약 (S15P11A705-339).

고정하는 계약은 다섯이다.

    ① 플래그 off(기본값)면 keyword 조회가 호출되지 않고 응답은 벡터 순서 그대로다
       — 현행 검색과 동작이 같다
    ② 같은 요청·같은 limit 에서 on/off 의 후보 Record id 집합이 같다 — 허용되는
       차이는 순서뿐이다. 재정렬 후 두 번째 절단도 없다(반환 건수 동일).
       측정 지점은 문자열 병합 직전 = 이 API 의 반환값이다(P49 §4)
    ③ 신호와 match 한 후보는 위로 오고, 응답의 `similarity` 는 원래 코사인 그대로다
    ④ 조회·계산 실패는 오류가 아니라 강등이다 — 벡터 순서로 되돌아간다
    ⑤ 신호가 없으면(후보 Preset 없음·match 없음) 벡터 순서 그대로다

DB 는 가짜 커넥션으로 대체한다 — 여기서 재는 것은 재정렬 경로이지 SQL 이 아니다.
같은 계약의 오프라인 판은 tests/test_search_fusion.py §9(rerank 픽스처)에 있다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import numpy as np
import pytest

from app.cache.preset_cache import Preset, PresetSnapshot
from app.core.config import Settings
from app.service.search_service import SearchService


def _settings(monkeypatch, **overrides) -> Settings:
    from tests.test_unit import _ENV as env
    for k, v in {**env, **overrides}.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


PROFILE = "openai-text-embedding-3-small-1536-cosine-v1"

# 질의 임베딩과 나란한 축([1,0,0,0])의 Preset 만 후보가 된다(cos=1.0 ≥ floor 0.35).
# 직교 축([0,1,0,0])은 cos=0.0 이라 후보가 아니다.
QUERY_AXIS = [1.0, 0.0, 0.0, 0.0]
OTHER_AXIS = [0.0, 1.0, 0.0, 0.0]


def _preset(pid: int, vec: list[float]) -> Preset:
    return Preset(
        id=pid, code=f"P{pid}", display_name="", category="", description="",
        examples=[], visibility="PUBLIC", version=1,
        embedding=np.asarray(vec, dtype=np.float32),
    )


class _Presets:
    """PresetCache 자리에 꽂는 스냅샷 스텁.

    BLOCKED 제외는 실물 `PresetCache.load` 의 책임이고 그 계약은
    test_pipeline(시나리오 15)이 고정한다 — 여기서는 적재된 뒤의 후보 계산만 잰다.
    """

    def __init__(self, presets: list[Preset]):
        self._snapshot = PresetSnapshot(presets=tuple(presets), version=1)

    def snapshot(self) -> PresetSnapshot:
        return self._snapshot


class _FakeEmbedding:
    async def embed_one(self, text: str):
        return list(QUERY_AXIS)


class _FakeDb:
    @asynccontextmanager
    async def acquire(self):
        yield None


# 컷을 전부 통과하는 행 3건 (단어형 질의 `부캠`: tau_word 0.24 · r 0.6×0.50=0.30).
# 102 가 keyword match 를 받으면 0.48+0.05=0.53 > 0.50 으로 1위에 온다.
ROWS = [
    {"record_id": 101, "context_id": 11, "similarity": 0.50},
    {"record_id": 102, "context_id": 12, "similarity": 0.48},
    {"record_id": 103, "context_id": 13, "similarity": 0.40},
]


@pytest.fixture
def vector_rows(monkeypatch):
    async def _rows(conn, user_id, profile, embedding, limit):
        return [dict(r) for r in ROWS]

    monkeypatch.setattr(
        "app.service.search_service.context_embedding_repo.search", _rows
    )


class _FakeKeywordRepo:
    """keywords_for_records 자리에 꽂는 가짜 — 호출 수와 반환·오류를 제어한다."""

    def __init__(self, rows=None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.calls = 0

    async def __call__(self, conn, user_id, record_ids):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.rows)


def _service(monkeypatch, settings, presets=None, keyword_rows=None, error=None):
    repo = _FakeKeywordRepo(rows=keyword_rows, error=error)
    monkeypatch.setattr(
        "app.service.search_service.context_keyword_repo.keywords_for_records", repo
    )
    cache = _Presets(presets) if presets is not None else None
    service = SearchService(
        _FakeDb(), _FakeEmbedding(), settings, preset_cache=cache
    )
    return service, repo


async def _search(service):
    return await service.search(1, "부캠", 20, PROFILE)


# match: preset 1(질의와 나란한 축) 을 record 102 가 가진다.
MATCH_102 = [{"record_id": 102, "keyword_id": 1}]
ALIGNED = [_preset(1, QUERY_AXIS), _preset(2, OTHER_AXIS)]


@pytest.mark.anyio
async def test_flag_off_never_reads_keywords_and_keeps_vector_order(
    monkeypatch, vector_rows
):
    """① 기본값(off)에서 keyword 조회 0회, 응답은 벡터 순서 그대로다."""
    settings = _settings(monkeypatch)
    assert settings.search_keyword_rerank_enabled is False
    service, repo = _service(
        monkeypatch, settings, presets=ALIGNED, keyword_rows=MATCH_102
    )
    result = await _search(service)
    assert repo.calls == 0
    assert [r["recordId"] for r in result] == [101, 102, 103]


@pytest.mark.anyio
async def test_candidate_set_is_invariant_between_on_and_off(
    monkeypatch, vector_rows
):
    """② on/off 의 후보 Record id 집합이 같다 — 차이는 순서뿐, 2차 절단 없음."""
    off_settings = _settings(monkeypatch)
    service, _ = _service(
        monkeypatch, off_settings, presets=ALIGNED, keyword_rows=MATCH_102
    )
    off = await _search(service)

    on_settings = _settings(monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true")
    service, repo = _service(
        monkeypatch, on_settings, presets=ALIGNED, keyword_rows=MATCH_102
    )
    on = await _search(service)

    assert repo.calls == 1
    assert {r["recordId"] for r in on} == {r["recordId"] for r in off}
    assert len(on) == len(off), "재정렬 후 두 번째 절단이 있어서는 안 된다"
    assert [r["recordId"] for r in on] != [r["recordId"] for r in off], \
        "이 픽스처는 순서가 바뀌는 조건이다 — 안 바뀌면 재정렬이 죽은 것이다"


@pytest.mark.anyio
async def test_matched_record_moves_up_and_similarity_is_untouched(
    monkeypatch, vector_rows
):
    """③ match 후보(102)가 1위로 오고 similarity 는 원래 코사인 그대로다."""
    settings = _settings(monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true")
    service, _ = _service(
        monkeypatch, settings, presets=ALIGNED, keyword_rows=MATCH_102
    )
    result = await _search(service)
    assert [r["recordId"] for r in result] == [102, 101, 103]
    assert {r["recordId"]: r["similarity"] for r in result} == {
        101: 0.50, 102: 0.48, 103: 0.40,
    }, "정렬 점수(코사인+weight)가 응답에 새어 나가면 안 된다"


@pytest.mark.anyio
async def test_fetch_failure_degrades_to_vector_order(monkeypatch, vector_rows):
    """④ keyword 조회 실패는 강등 — 응답이 실패하지 않고 벡터 순서를 돌려준다."""
    settings = _settings(monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true")
    service, repo = _service(
        monkeypatch, settings, presets=ALIGNED, error=RuntimeError("db down")
    )
    result = await _search(service)
    assert repo.calls == 1
    assert [r["recordId"] for r in result] == [101, 102, 103]


@pytest.mark.anyio
async def test_no_candidate_above_floor_skips_keyword_read(
    monkeypatch, vector_rows
):
    """⑤ 후보 Preset 이 없으면(코사인 전부 floor 미만) 조회 없이 벡터 순서다."""
    settings = _settings(monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true")
    service, repo = _service(
        monkeypatch, settings,
        presets=[_preset(2, OTHER_AXIS)], keyword_rows=MATCH_102,
    )
    result = await _search(service)
    assert repo.calls == 0
    assert [r["recordId"] for r in result] == [101, 102, 103]


@pytest.mark.anyio
async def test_no_match_keeps_vector_order(monkeypatch, vector_rows):
    """⑤ 조회는 됐는데 후보 Preset 과 match 가 없으면 벡터 순서 그대로다."""
    settings = _settings(monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true")
    service, repo = _service(
        monkeypatch, settings, presets=ALIGNED,
        keyword_rows=[{"record_id": 102, "keyword_id": 2}],  # 후보(1)가 아닌 keyword
    )
    result = await _search(service)
    assert repo.calls == 1
    assert [r["recordId"] for r in result] == [101, 102, 103]


@pytest.mark.anyio
async def test_flag_on_without_cache_uses_vector_order(monkeypatch, vector_rows):
    """캐시 미주입이면 플래그가 켜져 있어도 벡터 순서다 — 조립 실수의 방어선."""
    settings = _settings(monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true")
    service, repo = _service(
        monkeypatch, settings, presets=None, keyword_rows=MATCH_102
    )
    result = await _search(service)
    assert repo.calls == 0
    assert [r["recordId"] for r in result] == [101, 102, 103]


def test_defaults_are_the_measured_values(monkeypatch):
    """채택값이 조용히 바뀌면 재정렬은 남고 -339 실측 근거만 사라진다."""
    s = _settings(monkeypatch)
    assert s.search_keyword_rerank_enabled is False
    assert s.search_keyword_rerank_floor == 0.35
    assert s.search_keyword_rerank_weight == 0.05
    assert s.search_keyword_rerank_top_k == 3



# ── keywordMatched 필드 (S15P11A705-399) ──────────────────────────────────
#
# 재정렬이 이미 계산하는 match 여부를 응답에 싣는다. **재정렬 자신의 계약(위 5개)과는
# 별개다** — 여기서 재는 것은 "필드 값이 실제 match 를 반영하는가"이지 순서가 아니다.


@pytest.mark.anyio
async def test_keyword_matched_is_true_only_for_the_matched_record(
    monkeypatch, vector_rows
):
    settings = _settings(monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true")
    service, _ = _service(
        monkeypatch, settings, presets=ALIGNED, keyword_rows=MATCH_102
    )
    result = await _search(service)
    assert {r["recordId"]: r["keywordMatched"] for r in result} == {
        101: False, 102: True, 103: False,
    }


@pytest.mark.anyio
async def test_keyword_matched_is_false_for_all_when_flag_off(
    monkeypatch, vector_rows
):
    settings = _settings(monkeypatch)
    service, _ = _service(
        monkeypatch, settings, presets=ALIGNED, keyword_rows=MATCH_102
    )
    result = await _search(service)
    assert all(r["keywordMatched"] is False for r in result)


@pytest.mark.anyio
async def test_keyword_matched_is_false_for_all_when_fetch_fails(
    monkeypatch, vector_rows
):
    settings = _settings(monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true")
    service, _ = _service(
        monkeypatch, settings, presets=ALIGNED, error=RuntimeError("db down")
    )
    result = await _search(service)
    assert all(r["keywordMatched"] is False for r in result)


@pytest.mark.anyio
async def test_keyword_matched_is_false_for_all_when_no_candidate_preset(
    monkeypatch, vector_rows
):
    settings = _settings(monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true")
    service, _ = _service(
        monkeypatch, settings,
        presets=[_preset(2, OTHER_AXIS)], keyword_rows=MATCH_102,
    )
    result = await _search(service)
    assert all(r["keywordMatched"] is False for r in result)


def test_candidate_rule_matches_the_harness(monkeypatch):
    """floor 를 top_k 보다 먼저 걸고 동점은 preset_id 오름차순 — 하네스와 같은 규칙.

    잰 규칙(tools/search_cut/fusion.preset_candidates)과 돌리는 규칙이 갈리면
    채택값의 근거가 사라진다. 축 상 동일한 Preset 셋으로 동점 처리를 고정한다.
    """
    settings = _settings(
        monkeypatch, SEARCH_KEYWORD_RERANK_ENABLED="true",
        SEARCH_KEYWORD_RERANK_TOP_K="2",
    )
    service = SearchService(
        None, None, settings,
        preset_cache=_Presets(
            [_preset(3, QUERY_AXIS), _preset(1, QUERY_AXIS), _preset(2, QUERY_AXIS)]
        ),
    )
    assert service._preset_candidates(QUERY_AXIS) == {1, 2}

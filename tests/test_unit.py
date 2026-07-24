"""단위 계층 — DB·외부 IO 없음. 순수 함수·검증 로직."""
from __future__ import annotations

import numpy as np
import pytest

from app.cache.preset_cache import Preset, PresetSnapshot
from app.core.config import Settings
from app.core.errors import PermanentError, ProfileMismatchError, TransientError
from app.schema.llm import JudgeResult, KeywordSelection
from app.service.keyword_service import KeywordService, _topk
from tests.fakes import deterministic_vector

_ENV = {
    "DATABASE_URL": "postgresql://x:y@localhost:5432/db",
    "GMS_API_KEY": "k",
    "GMS_BASE_URL": "https://gms.example/gmsapi/api.openai.com/v1",
    "PINLOG_EMBEDDING_MODEL": "text-embedding-3-small",
    "PINLOG_EMBEDDING_DIMENSION": "1536",
    "PINLOG_EMBEDDING_DISTANCE": "cosine",
    "PINLOG_EMBEDDING_PROFILE": "openai-text-embedding-3-small-1536-cosine-v1",
    "INTERNAL_SHARED_SECRET": "s",
}


def _preset(id: int, code: str, vec: list[float]) -> Preset:
    return Preset(
        id=id, code=code, display_name=code, category="C", description="d",
        examples=[], visibility="PUBLIC", version=1,
        embedding=np.asarray(vec, dtype=np.float32),
    )


# ── TOP-K ──────────────────────────────────────────────
def test_topk_excludes_below_floor():
    q = deterministic_vector("query-text")
    far = deterministic_vector("완전히 무관한 다른 텍스트")
    snap = PresetSnapshot(presets=(_preset(1, "NEAR", q), _preset(2, "FAR", far)), version=1)
    got = _topk(np.asarray(q, dtype=np.float32), snap, k=10, floor=0.99)
    assert [p.id for p in got] == [1]  # 자기 자신(cos≈1)만 통과, 무관 벡터 제외


def test_topk_orders_by_similarity_and_respects_k():
    q = deterministic_vector("q")
    snap = PresetSnapshot(
        presets=(_preset(1, "A", q), _preset(2, "B", deterministic_vector("b")),
                 _preset(3, "C", deterministic_vector("c"))),
        version=1,
    )
    got = _topk(np.asarray(q, dtype=np.float32), snap, k=1, floor=-1.0)
    assert got[0].id == 1 and len(got) == 1  # 최고 유사도 1개


def test_topk_zero_candidates_on_empty_snapshot():
    snap = PresetSnapshot(presets=(), version=1)
    assert _topk(np.asarray(deterministic_vector("q"), dtype=np.float32), snap, 10, 0.3) == []


# ── LLM 매핑·폐기 ──────────────────────────────────────
def _svc() -> KeywordService:
    return KeywordService(db=None, llm_client=None, preset_cache=None, settings=None)  # type: ignore


def test_map_drops_out_of_candidate_ids():
    r = JudgeResult(selected=[KeywordSelection(1, 0.9), KeywordSelection(99, 0.8)])
    assert _svc()._map(r, {1, 2}, context_id=1) == [(1, 0.9)]


def test_map_drops_out_of_range_confidence():
    r = JudgeResult(selected=[KeywordSelection(1, 1.5), KeywordSelection(2, 0.4)])
    assert _svc()._map(r, {1, 2}, context_id=1) == [(2, 0.4)]


def test_map_dedupes_keeping_max_confidence():
    r = JudgeResult(selected=[KeywordSelection(1, 0.3), KeywordSelection(1, 0.7)])
    assert _svc()._map(r, {1}, context_id=1) == [(1, 0.7)]


def test_map_empty_is_empty():
    assert _svc()._map(JudgeResult(selected=[]), {1}, context_id=1) == []


# ── 오류 분류 ──────────────────────────────────────────
def test_error_hierarchy():
    assert issubclass(PermanentError, Exception)
    assert issubclass(TransientError, Exception)
    exc = ProfileMismatchError("a", "b")
    assert exc.request_profile == "a" and exc.server_profile == "b"


# ── Profile 검증 (기동 시 불일치 실패) ──────────────────
def test_settings_rejects_profile_model_mismatch(monkeypatch):
    for k, v in {**_ENV, "PINLOG_EMBEDDING_PROFILE": "openai-wrong-model-1536-cosine-v1"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(Exception):  # ValidationError: model 토큰이 profile에 없음
        Settings(_env_file=None)


def test_settings_accepts_consistent_profile(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.embedding_dimension == 1536
    assert s.embedding_model in s.embedding_profile

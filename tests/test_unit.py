"""단위 계층 — DB·외부 IO 없음. 순수 함수·검증 로직."""
from __future__ import annotations

import numpy as np
import pytest

from app.cache.preset_cache import Preset, PresetSnapshot
from app.core.config import Settings, SettingsError
from app.core.errors import PermanentError, ProfileMismatchError, TransientError
from app.schema.llm import JudgeResult, KeywordSelection
from app.service.keyword_service import KeywordService, _topk
from app.smoke import gms_roundtrip
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


# ── 공개 설정의 정본은 코드다 (P45) ─────────────────────
_PUBLIC_KEYS = (
    "PINLOG_EMBEDDING_MODEL",
    "PINLOG_EMBEDDING_DIMENSION",
    "PINLOG_EMBEDDING_DISTANCE",
    "PINLOG_EMBEDDING_PROFILE",
)


def test_public_settings_fall_back_to_code_defaults(monkeypatch):
    """Profile 넷은 주입하지 않아도 뜬다 — 정본이 코드에 있고 주입은 덮어쓰기다.

    이 테스트가 없으면 P45의 핵심 주장("주입은 필수가 아니다")에 근거가 없다.
    """
    for k in _PUBLIC_KEYS:
        monkeypatch.delenv(k, raising=False)
    for k, v in _ENV.items():
        if k not in _PUBLIC_KEYS:
            monkeypatch.setenv(k, v)

    s = Settings(_env_file=None)

    assert s.embedding_model == "text-embedding-3-small"
    assert s.embedding_dimension == 1536
    assert s.embedding_distance == "cosine"
    assert s.embedding_profile == "openai-text-embedding-3-small-1536-cosine-v1"


def test_code_defaults_satisfy_profile_consistency(monkeypatch):
    """기본값 넷이 서로 정합해야 한다.

    정합 검사는 주입값에만 걸리는 것이 아니다. 코드 기본값이 어긋난 채 배포되면
    아무도 주입하지 않은 환경에서 기동이 죽는다 — 정본을 코드로 옮긴 이상
    그 정합도 코드가 책임진다.
    """
    for k in _PUBLIC_KEYS:
        monkeypatch.delenv(k, raising=False)
    for k, v in _ENV.items():
        if k not in _PUBLIC_KEYS:
            monkeypatch.setenv(k, v)

    s = Settings(_env_file=None)

    for token in (s.embedding_model, str(s.embedding_dimension), s.embedding_distance):
        assert token in s.embedding_profile


def test_injection_overrides_code_defaults(monkeypatch):
    """덮어쓰기 경로가 살아 있어야 실험·롤백이 가능하다.

    기본값을 넣으면서 `alias`를 유지한 이유가 이것이다.
    """
    override = {
        "PINLOG_EMBEDDING_MODEL": "text-embedding-3-large",
        "PINLOG_EMBEDDING_DIMENSION": "3072",
        "PINLOG_EMBEDDING_DISTANCE": "cosine",
        "PINLOG_EMBEDDING_PROFILE": "openai-text-embedding-3-large-3072-cosine-v1",
    }
    for k, v in {**_ENV, **override}.items():
        monkeypatch.setenv(k, v)

    s = Settings(_env_file=None)

    assert s.embedding_model == "text-embedding-3-large"
    assert s.embedding_dimension == 3072
    assert s.embedding_profile == override["PINLOG_EMBEDDING_PROFILE"]


# ── GMS_BASE_URL 형식 (기동 시 fail-fast, ai#32 §2) ─────
def _settings_with(monkeypatch, **overrides) -> Settings:
    for k, v in {**_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_settings_rejects_gms_base_url_without_gmsapi_segment(monkeypatch):
    """세그먼트가 빠지면 임베딩만 살고 judge가 조용히 죽는다 — 기동을 막는다."""
    with pytest.raises(SettingsError):
        _settings_with(monkeypatch, GMS_BASE_URL="https://gateway.invalid/api.openai.com/v1")


def test_settings_rejects_gms_base_url_ending_at_gmsapi(monkeypatch):
    """`/gmsapi`로 끝나면 뒤에 붙일 경로가 없어 세그먼트로 인정하지 않는다."""
    with pytest.raises(SettingsError):
        _settings_with(monkeypatch, GMS_BASE_URL="https://gateway.invalid/gmsapi")


def test_settings_accepts_gms_base_url_with_gmsapi_segment(monkeypatch):
    assert "/gmsapi/" in _settings_with(monkeypatch).gms_base_url


def test_gms_base_url_error_carries_no_values(monkeypatch):
    """기동 실패 메시지는 배포 로그에 남는다 — URL·키가 실리면 안 된다.

    pydantic의 ValueError 경로를 쓰면 ValidationError가 input_value(원시 입력 dict)를
    메시지에 넣어 이 단언이 깨진다. SettingsError를 쓰는 이유가 이것이다.
    """
    url = "https://gateway.invalid/no-segment/v1"
    key = "gms-api-key-placeholder-sentinel"
    with pytest.raises(SettingsError) as excinfo:
        _settings_with(monkeypatch, GMS_BASE_URL=url, GMS_API_KEY=key)
    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert url not in rendered
    assert key not in rendered
    assert _ENV["DATABASE_URL"] not in rendered


# ── GMS 스모크 집계·출력 (실호출 없음, ai#32 §3) ────────
def _stub_checks(monkeypatch, *, embedding_exc=None, judge_exc=None) -> list[str]:
    """_CHECKS를 호출 기록용 스텁으로 교체하고 호출 순서 리스트를 돌려준다."""
    called: list[str] = []

    def _stub(name, exc):
        async def _run(_settings):
            called.append(name)
            if exc is not None:
                raise exc

        return _run

    monkeypatch.setattr(
        gms_roundtrip,
        "_CHECKS",
        (("embedding", _stub("embedding", embedding_exc)),
         ("judge", _stub("judge", judge_exc))),
    )
    return called


async def test_smoke_runs_judge_even_when_embedding_fails(monkeypatch):
    """비대칭 장애를 한 번에 보려면 앞선 실패로 뒤 검사를 건너뛰면 안 된다."""
    called = _stub_checks(monkeypatch, embedding_exc=TransientError("x"))
    results = await gms_roundtrip.run_checks(None)
    assert called == ["embedding", "judge"]
    assert results == [("embedding", "TransientError"), ("judge", None)]


async def test_smoke_records_type_name_not_exception_message(monkeypatch):
    """클라이언트 예외 메시지에는 URL·응답 본문 일부가 섞여 온다 — 타입만 남겨야 한다."""
    marker = "response-body-marker-that-must-not-surface"
    _stub_checks(monkeypatch, judge_exc=TransientError(f"llm error: 401 {marker}"))
    results = await gms_roundtrip.run_checks(None)
    assert ("judge", "TransientError") in results
    assert marker not in "".join(f"{n}{f}" for n, f in results)


async def test_smoke_all_pass(monkeypatch):
    _stub_checks(monkeypatch)
    assert await gms_roundtrip.run_checks(None) == [("embedding", None), ("judge", None)]


def test_smoke_report_exit_code_zero_only_when_all_pass(capsys):
    assert gms_roundtrip.report([("embedding", None), ("judge", None)]) == 0
    assert gms_roundtrip.report([("embedding", None), ("judge", "TransientError")]) == 1
    assert gms_roundtrip.report([("embedding", "PermanentError"), ("judge", None)]) == 1
    out = capsys.readouterr().out
    assert "SMOKE FAILED: judge" in out and "SMOKE FAILED: embedding" in out

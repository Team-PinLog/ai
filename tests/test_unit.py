"""단위 계층 — DB·외부 IO 없음. 순수 함수·검증 로직."""
from __future__ import annotations

import numpy as np
import pytest

from app.cache.preset_cache import Preset, PresetSnapshot
from app.client.embedding_client import _TIMEOUT as EMB_TIMEOUT
from app.client.llm_client import _TIMEOUT as LLM_TIMEOUT
from app.client.retry import RetryPolicy
from app.core.config import Settings, SettingsError
from app.core.errors import (
    PermanentError,
    ProfileMismatchError,
    SchemaViolationError,
    TransientError,
    classify_http_status,
)
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


def test_schema_violation_is_transient_subtype():
    # §2.2 승격 전까지는 재시도 대상이어야 하고, service가 보는 분류는 두 종류로 유지된다.
    assert issubclass(SchemaViolationError, TransientError)
    assert not issubclass(SchemaViolationError, PermanentError)


# failure-recovery.md §2.1·§2.2 표를 그대로 옮긴 대조. HTTP 없이 매핑만 본다.
@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_transient_status_codes(code):
    assert isinstance(classify_http_status(code, "d"), TransientError)


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 413, 422])
def test_permanent_status_codes(code):
    assert isinstance(classify_http_status(code, "d"), PermanentError)


def test_429_is_not_swallowed_by_5xx_rule():
    # 결함 2의 형태: `>= 500`만 보면 429가 4xx로 떨어져 영구 오류가 된다.
    assert type(classify_http_status(429, "d")) is TransientError
    assert type(classify_http_status(499, "d")) is PermanentError


def test_classify_preserves_detail_message():
    assert "boom" in str(classify_http_status(503, "boom"))


# ── 짧은 재시도 정책 (§3.1·§3.2) ────────────────────────
def test_backoff_is_exponential_and_capped():
    p = RetryPolicy(attempts=6, base_delay=0.5, multiplier=2.0, max_delay=4.0,
                    jitter=lambda d: d)
    assert [p.delay_for(i) for i in range(5)] == [0.5, 1.0, 2.0, 4.0, 4.0]


def test_default_policy_is_two_retries():
    # §3.1 "최대 2회 (총 3회 시도)"
    assert RetryPolicy().attempts == 3


def test_full_jitter_stays_within_backoff():
    p = RetryPolicy()  # 기본 jitter = full jitter
    for i in range(p.attempts - 1):
        ceiling = p.base_delay * p.multiplier**i
        assert all(0.0 <= p.delay_for(i) <= ceiling for _ in range(100))


def test_jitter_actually_varies():
    # jitter가 상수를 반환하면 재시도가 다시 몰린다. 값이 흩어지는지 본다.
    p = RetryPolicy()
    assert len({p.delay_for(1) for _ in range(50)}) > 1


def test_attempts_must_include_the_first_call():
    with pytest.raises(ValueError):
        RetryPolicy(attempts=0)


def test_retry_budget_fits_processing_expiry():
    """§3.2: 두 호출의 타임아웃 합 + 재시도 대기가 PROCESSING 만료(600s)를 넘지 않는다.

    넘으면 재스캔이 아직 살아 있는 작업을 중복 실행해 비용이 두 배가 된다.
    """
    p = RetryPolicy()
    worst = p.attempts * (EMB_TIMEOUT + LLM_TIMEOUT) + 2 * p.worst_case_delay
    assert worst < Settings.model_fields["processing_expiry_sec"].default


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


# ── 판정 벤더 폴백 체인 (S15P11A705-175) ────────────────
def _settings_with(monkeypatch, **overrides) -> Settings:
    for k, v in {**_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_default_judge_chain_is_the_measured_priority_order(monkeypatch):
    """2026-07-30 실측으로 확정한 순서다. 조용히 바뀌면 시연 당일 동작이 달라진다.

    1순위 gpt-4o-mini(100% · 0.91s), 2순위 gemini-2.5-flash(현행 기준선),
    3순위 claude-haiku(프로바이더가 셋째라 동시 장애 가능성이 가장 낮다).
    """
    monkeypatch.delenv("PINLOG_JUDGE_CHAIN", raising=False)
    assert _settings_with(monkeypatch).judge_vendors == (
        ("openai", "gpt-4o-mini"),
        ("gemini", "gemini-2.5-flash"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    )


def test_judge_model_is_the_first_link_of_the_chain(monkeypatch):
    """진단 도구와 `model_profile` 기본값이 읽는 값 — 두 곳이 갈라지지 않게 파생시킨다."""
    s = _settings_with(monkeypatch, PINLOG_JUDGE_CHAIN="gemini:only-model")
    assert s.judge_model == "only-model"


def test_judge_chain_can_be_rolled_back_to_a_single_vendor(monkeypatch):
    """완료 조건 — 단일 벤더로 되돌리는 것이 설정만으로 된다."""
    s = _settings_with(monkeypatch, PINLOG_JUDGE_CHAIN="gemini:gemini-2.5-flash")
    assert s.judge_vendors == (("gemini", "gemini-2.5-flash"),)


def test_judge_chain_order_is_configurable(monkeypatch):
    """완료 조건 — 순서를 코드 수정 없이 바꿀 수 있다."""
    s = _settings_with(
        monkeypatch, PINLOG_JUDGE_CHAIN="anthropic:c, gemini:g ,openai:o"
    )
    assert s.judge_vendors == (("anthropic", "c"), ("gemini", "g"), ("openai", "o"))


@pytest.mark.parametrize("spec", ["gpt-4o-mini", "openai:", ":gpt-4o-mini", "openai:a,bad"])
def test_judge_chain_rejects_malformed_entries(monkeypatch, spec):
    """벤더 없는 모델명만으로는 어느 경로·어느 인증 헤더로 부를지 알 수 없다."""
    with pytest.raises(SettingsError, match="PINLOG_JUDGE_CHAIN"):
        _settings_with(monkeypatch, PINLOG_JUDGE_CHAIN=spec)


@pytest.mark.parametrize("spec", ["", "   ", ","])
def test_judge_chain_rejects_an_empty_chain(monkeypatch, spec):
    """벤더가 하나도 없으면 Keyword 생성 경로 전체가 죽는다 — 기동에서 막는다."""
    with pytest.raises(SettingsError, match="비어 있"):
        _settings_with(monkeypatch, PINLOG_JUDGE_CHAIN=spec)


def test_judge_chain_is_validated_at_startup_not_at_first_request(monkeypatch):
    """판정 경로는 첫 Context 요청까지 실행되지 않는다. 그때까지 미루면 서버는 정상으로
    보이는데 Keyword만 통째로 안 생기는 비대칭 장애가 된다."""
    for k, v in {**_ENV, "PINLOG_JUDGE_CHAIN": "openai"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SettingsError):
        Settings(_env_file=None)          # 속성 접근 없이 생성만으로 터진다


def test_judge_chain_error_names_the_offending_entry(monkeypatch):
    """모델명은 공개 값이다(P45) — 무엇이 잘못됐는지 보여야 고칠 수 있다."""
    with pytest.raises(SettingsError) as excinfo:
        _settings_with(monkeypatch, PINLOG_JUDGE_CHAIN="openai:gpt-4o-mini,typo-here")
    assert "typo-here" in str(excinfo.value)


def test_judge_chain_error_carries_no_secrets(monkeypatch):
    """기동 실패 메시지는 배포 로그에 남는다 — 체인 오류에도 키·URL이 실리면 안 된다."""
    key = "gms-api-key-placeholder-sentinel"
    with pytest.raises(SettingsError) as excinfo:
        _settings_with(monkeypatch, GMS_API_KEY=key, PINLOG_JUDGE_CHAIN="broken")
    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert key not in rendered
    assert _ENV["DATABASE_URL"] not in rendered


# ── GMS_BASE_URL 형식 (기동 시 fail-fast, ai#32 §2) ─────


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


# ── Preset 캐시 스냅샷 (S15P11A705-110) ─────────────────
def test_snapshot_as_dicts_exposes_only_judgement_fields():
    """LLM 입력에 code·visibility·version·embedding이 섞이면 프롬프트가 오염되고
    토큰만 늘어난다. 내보내는 필드를 값으로 고정한다."""
    snap = PresetSnapshot(presets=(_preset(1, "WITH_FRIENDS", [0.0, 1.0]),), version=3)
    assert snap.as_dicts() == [
        {"id": 1, "display_name": "WITH_FRIENDS", "category": "C",
         "description": "d", "examples": []}
    ]


def test_preset_cache_snapshot_before_load_raises():
    """적재 전 조회는 조용한 빈 결과가 아니라 오류다 — 빈 후보로 COMPLETED를 쓰면
    데이터가 조용히 망가진다(keyword-preset.md §2)."""
    from app.cache.preset_cache import PresetCache

    with pytest.raises(RuntimeError, match="not loaded"):
        PresetCache().snapshot()


# ── pgvector ↔ 파이썬 변환 (양쪽 분기) ──────────────────
class _VectorLike:
    """asyncpg가 돌려주는 pgvector.Vector의 최소 모양."""

    def __init__(self, values: list[float]) -> None:
        self._values = values

    def to_numpy(self):
        return np.asarray(self._values, dtype=np.float64)

    def to_list(self):
        return list(self._values)


def test_keyword_to_array_accepts_both_vector_and_plain_list():
    from app.service.keyword_service import _to_array

    assert _to_array(_VectorLike([1.0, 2.0])).dtype == np.float32
    assert _to_array([1.0, 2.0]).tolist() == [1.0, 2.0]


def test_embedding_to_list_accepts_both_vector_and_plain_list():
    """저장 경로는 `to_list`를 가진 pgvector 값과 Fake가 주는 순수 list를 모두 받는다.
    한쪽만 다루면 재사용 경로가 운영에서만 깨진다."""
    from app.service.embedding_service import _to_list

    assert _to_list(_VectorLike([1.0, 2.0])) == [1.0, 2.0]
    assert _to_list((1.0, 2.0)) == [1.0, 2.0]


def test_map_keeps_first_confidence_when_duplicate_is_lower():
    """중복 접기는 최댓값 기준이다. 내림차순으로 와도 뒤의 낮은 값이 앞을 덮지 않는다 —
    `test_map_dedupes_keeping_max_confidence`(오름차순)의 짝."""
    r = JudgeResult(selected=[KeywordSelection(1, 0.7), KeywordSelection(1, 0.3)])
    assert _svc()._map(r, {1}, context_id=1) == [(1, 0.7)]


def test_map_keeps_confidence_when_duplicate_has_none():
    """confidence 없는 중복이 값 있는 선택을 None으로 덮으면 안 된다."""
    r = JudgeResult(selected=[KeywordSelection(1, 0.7), KeywordSelection(1, None)])
    assert _svc()._map(r, {1}, context_id=1) == [(1, 0.7)]


# ── 스모크 엔트리포인트 (실호출 없음) ───────────────────
@pytest.fixture
def _restore_http_log_levels():
    import logging

    saved = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def test_silence_http_logging_raises_httpx_levels_above_last_resort(_restore_http_log_levels):
    """httpx는 요청마다 INFO로 전체 URL을 남긴다. 핸들러가 없을 때의 lastResort(WARNING+)
    까지 막아야 배포 로그에 endpoint가 남지 않는다(ai#32 §3)."""
    import logging

    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)

    gms_roundtrip._silence_http_logging()

    assert logging.getLogger("httpx").level == logging.CRITICAL
    assert logging.getLogger("httpcore").level == logging.CRITICAL


class _StubEmbeddingClient:
    def __init__(self, *, base_url, api_key, model, dimension):
        self.dimension = dimension

    async def embed_one(self, text: str) -> list[float]:
        return [0.0] * self.dimension


class _StubLLMClient:
    def __init__(self, *, gms_base_url, api_key, chain):
        pass

    async def judge(self, context_text: str, candidates: list[dict]) -> JudgeResult:
        return JudgeResult(selected=[])


@pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
def test_smoke_module_runs_as_a_script_and_exits_zero(
    monkeypatch, capsys, _restore_http_log_levels
):
    """`python -m app.smoke.gms_roundtrip` 경로 — dev 배포 activation 게이트 그 자체.

    `if __name__ == "__main__"` 아래는 import로 실행되지 않으므로 `runpy`로 스크립트 실행을
    재현한다. runpy는 새 네임스페이스를 쓰므로 캐시된 모듈 패치가 보이지 않는다 —
    **원본 클라이언트 모듈**의 속성을 갈아 끼워 새 네임스페이스의 import가 그것을 집게 한다.
    실제 GMS는 부르지 않는다(tests/README.md: 실호출을 CI에 넣지 않는다).
    """
    import runpy

    monkeypatch.setattr("app.client.embedding_client.EmbeddingClient", _StubEmbeddingClient)
    monkeypatch.setattr("app.client.llm_client.LLMClient", _StubLLMClient)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("app.smoke.gms_roundtrip", run_name="__main__")

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "embedding: ok" in out and "judge: ok" in out
    assert "OK: gms smoke passed (2 checks)" in out


@pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
def test_smoke_script_exits_nonzero_and_leaks_no_values_when_a_check_fails(
    monkeypatch, capsys, _restore_http_log_levels
):
    """실패해도 종료 코드만 1이고 응답 본문·URL은 새지 않는다."""
    import runpy

    marker = "leaked-endpoint-and-body-fragment"

    class _FailingLLM(_StubLLMClient):
        async def judge(self, context_text, candidates):
            raise PermanentError(f"llm error: 401 {marker}")

    monkeypatch.setattr("app.client.embedding_client.EmbeddingClient", _StubEmbeddingClient)
    monkeypatch.setattr("app.client.llm_client.LLMClient", _FailingLLM)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("app.smoke.gms_roundtrip", run_name="__main__")

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "judge: failed (PermanentError)" in out
    assert "SMOKE FAILED: judge" in out
    assert marker not in out


# ── coverage 게이트 판정 (S15P11A705-110) ───────────────
# 게이트를 켜 놓고 "통과한다"만 확인하면 아무것도 강제하지 않는 게이트를 놓친다.
# 여기서 임계값 양쪽을 값으로 고정한다. 드릴(실제 RED 관측)은 PR 본문에 기록한다.
from tools.check_coverage_gate import CoverageGateError, evaluate  # noqa: E402


def _totals(*, lines=(80, 100), branches=(80, 100)) -> dict:
    return {
        "covered_lines": lines[0], "num_statements": lines[1],
        "covered_branches": branches[0], "num_branches": branches[1],
    }


def test_gate_passes_exactly_at_the_threshold():
    assert all(m.ok for m in evaluate(_totals(lines=(80, 100), branches=(80, 100))))


@pytest.mark.parametrize(
    "totals, failing",
    [
        (_totals(lines=(799, 1000)), "line"),      # 79.9% — line만 미달
        (_totals(branches=(799, 1000)), "branch"),  # 79.9% — branch만 미달
    ],
)
def test_gate_fails_when_either_metric_alone_is_below_threshold(totals, failing):
    """둘을 **따로** 본다는 것이 이 게이트의 전부다. 합산 비율(`--cov-fail-under`)이면
    statement 수에 가려 branch 미달이 통과한다."""
    metrics = {m.name: m.ok for m in evaluate(totals)}
    assert metrics[failing] is False
    assert metrics["line" if failing == "branch" else "branch"] is True


def test_gate_refuses_a_report_measured_without_cov_branch():
    """`--cov-branch` 없는 리포트에는 branch 키 자체가 없다(coverage.py 실측).
    '측정하지 못했다'를 통과로 처리하면 게이트가 사라진 것과 같다."""
    totals = _totals()
    del totals["num_branches"]
    del totals["covered_branches"]
    with pytest.raises(CoverageGateError, match="--cov-branch"):
        evaluate(totals)


def test_gate_refuses_a_report_with_zero_branches():
    """키는 있는데 0인 경우도 같은 이유로 막는다."""
    with pytest.raises(CoverageGateError, match="--cov-branch"):
        evaluate(_totals(branches=(0, 0)))


def test_gate_refuses_an_empty_report():
    with pytest.raises(CoverageGateError, match="statement 가 0"):
        evaluate(_totals(lines=(0, 0)))


def test_gate_thresholds_are_the_ticket_completion_criteria():
    """임계값이 조용히 내려가면 게이트는 남고 의미만 사라진다."""
    from tools import check_coverage_gate

    assert check_coverage_gate.LINE_MIN == 80.0
    assert check_coverage_gate.BRANCH_MIN == 80.0


# ── 개인 검색 결과 컷 (S15P11A705-213) ──────────────────
def _cut(monkeypatch, rows, **overrides):
    """`SearchService._cut` 만 부른다 — DB·임베딩 클라이언트를 타지 않는 순수 계산이다."""
    from app.service.search_service import SearchService

    settings = _settings_with(monkeypatch, **overrides)
    service = SearchService(db=None, embedding_client=None, settings=settings)
    return [r["similarity"] for r in service._cut([{"similarity": s} for s in rows])]


def test_cut_absolute_floor_drops_below_threshold(monkeypatch):
    got = _cut(monkeypatch, [0.80, 0.35, 0.29, 0.10],
               SEARCH_SIMILARITY_FLOOR="0.30", SEARCH_TOP_RATIO="0")
    assert got == [0.80, 0.35]


def test_cut_ratio_is_relative_to_top1(monkeypatch):
    """같은 0.35 가 1위에 따라 살기도 죽기도 한다 — 그것이 τ_abs 로 대체되지 않는 이유다."""
    high = _cut(monkeypatch, [0.80, 0.35], SEARCH_SIMILARITY_FLOOR="0", SEARCH_TOP_RATIO="0.60")
    low = _cut(monkeypatch, [0.50, 0.35], SEARCH_SIMILARITY_FLOOR="0", SEARCH_TOP_RATIO="0.60")
    assert high == [0.80]
    assert low == [0.50, 0.35]


def test_cut_ratio_baseline_is_the_pre_cut_top1(monkeypatch):
    """1위가 τ_abs 에 걸려 사라져도 r 의 기준은 **그 1위**다.

    살아남은 것의 1위로 기준을 옮기면 아무것도 더 잘리지 않는 자기충족 컷이 된다.
    """
    got = _cut(monkeypatch, [0.90, 0.40, 0.30],
               SEARCH_SIMILARITY_FLOOR="0.35", SEARCH_TOP_RATIO="0.60")
    # 기준 0.90×0.60=0.54 → 0.40·0.30 은 r 에서 이미 탈락. 0.90 은 τ_abs 를 넘는다
    assert got == [0.90]


def test_cut_can_return_zero_rows(monkeypatch):
    """무관 질의에 억지 결과를 내밀지 않는 것이 τ_abs 의 존재 이유다."""
    got = _cut(monkeypatch, [0.28, 0.22], SEARCH_SIMILARITY_FLOOR="0.30", SEARCH_TOP_RATIO="0.60")
    assert got == []


def test_cut_ratio_alone_never_empties(monkeypatch):
    """r 은 1위를 언제나 남긴다. 이것이 r 하나로 충분하지 않은 이유다."""
    got = _cut(monkeypatch, [0.05, 0.01], SEARCH_SIMILARITY_FLOOR="0", SEARCH_TOP_RATIO="0.90")
    assert got == [0.05]


def test_cut_disabled_when_both_zero(monkeypatch):
    rows = [0.80, 0.02]
    assert _cut(monkeypatch, rows, SEARCH_SIMILARITY_FLOOR="0", SEARCH_TOP_RATIO="0") == rows


def test_cut_defaults_are_the_measured_values(monkeypatch):
    """값이 조용히 바뀌면 컷은 남고 -213 의 측정 근거만 사라진다."""
    s = _settings_with(monkeypatch)
    assert s.search_similarity_floor == 0.30
    assert s.search_top_ratio == 0.60


# ── 질의 길이별 τ_abs (S15P11A705-266) ──────────────────
def _cut_q(monkeypatch, rows, query, **overrides):
    """질의를 함께 넘겨 `_cut` 을 부른다. 위 `_cut` 과 달리 길이 분기를 태운다."""
    from app.service.search_service import SearchService

    settings = _settings_with(monkeypatch, **overrides)
    service = SearchService(db=None, embedding_client=None, settings=settings)
    return [r["similarity"] for r in
            service._cut([{"similarity": s} for s in rows], query)]


def test_cut_word_query_uses_lower_floor(monkeypatch):
    """같은 0.26 이 질의 길이에 따라 살기도 죽기도 한다.

    이것이 `-266` 의 결론 전부다 — 두 대역이 겹치지 않아(문장형 정답 하한 0.3642 ·
    단어형 0.2438) 한 값으로는 한쪽이 반드시 손해를 본다. 문장형에 맞춘 0.30 이
    단어형에서 **컷 전 1위인 정답**을 잘라내던 것이 `ai#87` 이다.
    """
    assert _cut_q(monkeypatch, [0.26], "비건", SEARCH_TOP_RATIO="0") == [0.26]
    assert _cut_q(monkeypatch, [0.26], "채식 샌드위치 먹던 단골집", SEARCH_TOP_RATIO="0") == []


def test_cut_spaced_query_is_not_a_word_query(monkeypatch):
    """공백이 있으면 짧아도 문장형이다.

    `-266` 의 행렬에는 「공백 있고 짧은」 질의가 없어 두 정의(글자 수 · 어절 수)를 가르지
    못했다. 그래서 **둘 다 요구해** 애매한 질의를 문장형(더 세게 자름)으로 기울인다.
    """
    assert _cut_q(monkeypatch, [0.26], "신한 부캠", SEARCH_TOP_RATIO="0") == []


def test_cut_long_query_without_space_is_not_a_word_query(monkeypatch):
    """공백이 없어도 길면 문장형이다. 위와 같은 이유로 두 조건은 AND 다."""
    assert _cut_q(monkeypatch, [0.26], "혼자조용히작업하기좋은카페",
                  SEARCH_TOP_RATIO="0") == []


def test_cut_ratio_is_not_split_by_query_length(monkeypatch):
    """**`r` 은 가르지 않는다.** 상대 컷이라 대역 차이를 자동으로 흡수한다.

    실측에서도 단어형 정답 손실이 `r=0.75` 까지 0 이었다. 여기서 갈리면 `-266` 이 재지
    않은 축을 코드가 만든 것이 된다.
    """
    word = _cut_q(monkeypatch, [0.80, 0.40], "비건", SEARCH_SIMILARITY_FLOOR="0")
    sent = _cut_q(monkeypatch, [0.80, 0.40], "채식 샌드위치 먹던 단골집",
                  SEARCH_SIMILARITY_FLOOR="0")
    assert word == sent == [0.80]


def test_cut_word_query_boundary_is_exactly_max_chars(monkeypatch):
    """**「5자 이하」를 코드가 지키는지 고정한다.**

    이 값이 없으면 `len(q) <= max_chars` 를 `<` 로 바꿔도 전부 초록이고 커버리지도 100%
    그대로다. 그러면 명세의 「5자 이하」와 `config.py` 주석의 「2~5자」가 조용히 거짓이 된다
    — 「경계 5자는 측정이 아니라 판단」이라고 세 곳에 적어 둔 값인데 그 판단을 지키는 장치가
    없었다.
    """
    assert _cut_q(monkeypatch, [0.26], "아이스크림", SEARCH_TOP_RATIO="0") == [0.26]  # 5자
    assert _cut_q(monkeypatch, [0.26], "아이스크림콘", SEARCH_TOP_RATIO="0") == []  # 6자


def test_cut_word_query_treats_all_unicode_whitespace_as_a_separator(monkeypatch):
    """전각 공백·탭·NBSP 도 공백이다.

    U+0020 만 보면 `_is_word_query` 가 선언한 안전 방향(**애매하면 문장형으로 기운다**)이
    구분자만 바뀌면 **반대로** 뒤집힌다 — 2어절 질의가 「공백 없음」으로 통과해 오히려
    느슨한 0.24 를 탄다. 요청 스키마에 정규화가 없어 원문이 그대로 도달하므로(IME 전각
    모드·타 화면 복사) 실제로 닿는 경로다.
    """
    for sep in ("　", "\t", "\xa0", "\n"):
        assert _cut_q(monkeypatch, [0.26], f"신한{sep}부캠", SEARCH_TOP_RATIO="0") == [], sep


def test_cut_kill_switch_also_disables_the_word_floor(monkeypatch):
    """**비상 스위치는 분기보다 앞이다.**

    `-213` 이 이 가드를 넣었을 때 분기가 없었고 분기는 `-266` 이 만들었다. 가드를 분기
    뒤에 두면 `SEARCH_SIMILARITY_FLOOR=0` 으로 컷을 끄려 해도 단어형만 0.24 로 계속 잘리고,
    **장애 중에** 그것을 알아채야 한다. 나중에 생긴 것이 먼저 있던 안전장치를 무력화하면
    그것이 퇴행이다.
    """
    rows = [0.20, 0.05]
    off = dict(SEARCH_SIMILARITY_FLOOR="0", SEARCH_TOP_RATIO="0")
    assert _cut_q(monkeypatch, rows, "비건", **off) == rows  # 단어형도 꺼진다
    assert _cut_q(monkeypatch, rows, "채식 샌드위치 먹던 단골집", **off) == rows


def test_cut_word_floor_alone_does_not_disable_the_cut(monkeypatch):
    """`SEARCH_SIMILARITY_FLOOR_WORD=0` 은 **끄는 스위치가 아니다** — `r` 이 남아 계속 자른다.

    `config.py` 주석이 두 키의 성격을 갈라 적은 것(비상 스위치 / 튜닝 값)을 코드로 고정한다.
    """
    got = _cut_q(monkeypatch, [0.80, 0.20], "비건", SEARCH_SIMILARITY_FLOOR_WORD="0")
    assert got == [0.80]  # r=0.60 × 0.80 = 0.48 에 걸려 0.20 이 잘린다


def test_cut_word_defaults_are_the_measured_values(monkeypatch):
    """0.24 는 「컷 전 1위인 정답을 하나도 잃지 않는 가장 높은 값」이다(0.25 부터 깨진다).

    경계 5 자는 측정이 아니라 판단이다 — `-266` 의 단어형이 전부 2~5자라 두 정의를 가를
    수 없었고 `-255` 의 길이 상관이 쓴 값을 따랐다.
    """
    s = _settings_with(monkeypatch)
    assert s.search_similarity_floor_word == 0.24
    assert s.search_word_query_max_chars == 5


def test_search_limit_default_matches_public_contract():
    """공용 계약 08 §6.1 의 `size` 기본값 20. back 이 항상 명시해 보내 드러나지 않았다."""
    from app.schema.search import SearchRequest

    assert SearchRequest.model_fields["limit"].default == 20

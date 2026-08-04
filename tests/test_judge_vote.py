"""판정 n회 다수결 (S15P11A705-223).

이 파일이 지켜야 하는 것은 둘이다.

    n=1 이 현행과 정확히 같다      되돌리기가 설정 한 줄이라는 요구가 여기 걸려 있다
    부분 실패가 규칙을 안 바꾼다    분모를 성공 수로 낮추면 n 을 켠 채 n=1 이 실행된다

앞은 `test_n1_*`, 뒤는 `test_quorum_*` 이 본다. 순수 규칙은 `combine`·`has_quorum` 을
직접 부르고, 서비스 결선(예외 분류·저장·호출 수)은 `test_pipeline.py` 와 같은 Fake 조립을
쓴다 — 판정 경로의 계약은 저쪽이 정본이므로 자를 새로 만들지 않는다.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.cache.preset_cache import PresetCache
from app.core.config import Settings, SettingsError
from app.core.errors import PermanentError, TransientError
from app.repository import keyword_preset_repo
from app.schema.context import ContextProcessRequest
from app.schema.llm import JudgeResult, KeywordSelection
from app.service import judge_vote
from app.service.context_processing import ContextProcessingService
from app.service.embedding_service import EmbeddingService
from app.service.keyword_service import KeywordService
from tests.builders import make_embedding, make_preset, make_state
from tests.fakes import FakeEmbeddingClient, deterministic_vector


# ── 헬퍼 ────────────────────────────────────────────────
def _res(pairs, unmatched=None, model="m") -> JudgeResult:
    return JudgeResult(
        selected=[KeywordSelection(keyword_id=k, confidence=c) for k, c in pairs],
        unmatched_concepts=list(unmatched or []),
        model=model,
    )


def _ids(result: JudgeResult) -> list[int]:
    return sorted(s.keyword_id for s in result.selected)


class SequenceLLMClient:
    """회차마다 다른 답을 주는 Fake. **다수결 테스트에는 이것이 필수다** —
    `FakeLLMClient` 는 매번 같은 답을 주므로 어떤 n 에서도 결과가 같아 규칙이 검증되지
    않는다. `outcomes` 의 원소는 `JudgeResult` 이거나 던질 예외다."""

    def __init__(self, outcomes) -> None:
        self._outcomes = list(outcomes)
        self.call_count = 0

    async def judge(self, context_text: str, candidates: list[dict]) -> JudgeResult:
        # 동시 호출이라 인덱스를 먼저 잡고 await 한다.
        i = self.call_count
        self.call_count += 1
        await asyncio.sleep(0)
        out = self._outcomes[i % len(self._outcomes)]
        if isinstance(out, BaseException):
            raise out
        return out


# ── 규칙: 정족수 ────────────────────────────────────────
@pytest.mark.parametrize(
    "ok,n,expected",
    [(1, 1, True), (0, 1, False), (2, 3, True), (1, 3, False), (3, 5, True), (2, 5, False)],
)
def test_quorum_is_strict_majority_of_n(ok, n, expected):
    assert judge_vote.has_quorum(ok, n) is expected


# ── 규칙: 다수결 ────────────────────────────────────────
def test_n1_returns_the_single_result_unchanged():
    """n=1 은 현행과 구분되지 않아야 한다 — 한 표가 곧 선택이다."""
    out = judge_vote.combine([_res([(101, 0.9), (102, None)], ["개념"])], 1)
    assert _ids(out) == [101, 102]
    assert {s.keyword_id: s.confidence for s in out.selected} == {101: 0.9, 102: None}
    assert out.unmatched_concepts == ["개념"]


def test_majority_keeps_common_and_drops_shaky():
    """이 티켓의 전제 그 자체 — 2/3 은 남고 1/3 은 사라진다."""
    out = judge_vote.combine(
        [_res([(101, 0.9), (102, 0.8)]), _res([(101, 0.7)]), _res([(101, 0.8), (103, 0.5)])], 3
    )
    assert _ids(out) == [101]


def test_confidence_is_the_median_of_supporting_reps():
    out = judge_vote.combine(
        [_res([(101, 0.2)]), _res([(101, 0.9)]), _res([(101, 0.8)])], 3
    )
    assert out.selected[0].confidence == 0.8


def test_confidence_none_when_no_supporter_reported_one():
    out = judge_vote.combine([_res([(101, None)]), _res([(101, None)]), _res([])], 3)
    assert out.selected[0].confidence is None


def test_duplicate_within_one_rep_counts_as_one_vote():
    """한 회차가 같은 후보를 두 번 내도 표는 하나다. 아니면 회차 하나가 과반을 만든다."""
    out = judge_vote.combine([_res([(101, 0.9), (101, 0.8)]), _res([]), _res([])], 3)
    assert out.selected == []


def test_denominator_is_n_not_the_number_of_successes():
    """성공 2회뿐이어도 n=3 의 과반(2표)을 요구한다 — 한쪽만 고른 것은 떨어진다."""
    out = judge_vote.combine([_res([(101, 0.9), (102, 0.9)]), _res([(101, 0.7)])], 3)
    assert _ids(out) == [101]


def test_unmatched_concepts_follow_the_same_majority_rule():
    out = judge_vote.combine(
        [_res([], ["A", "B"]), _res([], ["A"]), _res([], ["C"])], 3
    )
    assert out.unmatched_concepts == ["A"]


def test_model_is_the_one_that_answered_most():
    out = judge_vote.combine(
        [_res([], model="x"), _res([], model="y"), _res([], model="y")], 3
    )
    assert out.model == "y"


def test_combine_rejects_n_below_one():
    with pytest.raises(ValueError):
        judge_vote.combine([_res([])], 0)


# ── 설정 ────────────────────────────────────────────────
def _settings(**over) -> Settings:
    base = dict(
        DATABASE_URL="postgresql://x/y",
        GMS_API_KEY="k",
        GMS_BASE_URL="https://gms.example/gmsapi/api.openai.com/v1",
        KAKAO_REST_API_KEY="kakao-k",
        INTERNAL_SHARED_SECRET="s",
    )
    return Settings(**{**base, **over})


def test_default_vote_n_is_one():
    """기본값이 1 이 아니면 이 티켓은 되돌릴 수 없는 변경이 된다."""
    assert _settings().judge_vote_n == 1


@pytest.mark.parametrize("bad", [0, -1, 2, 4])
def test_even_or_nonpositive_vote_n_stops_startup(bad):
    with pytest.raises((SettingsError, ValidationError)):
        _settings(PINLOG_JUDGE_VOTE_N=bad)


@pytest.mark.parametrize("good", [1, 3, 5])
def test_odd_vote_n_is_accepted(good):
    assert _settings(PINLOG_JUDGE_VOTE_N=good).judge_vote_n == good


# ── 서비스 결선 ──────────────────────────────────────────
async def _load_cache(conn, settings, specs):
    for s in specs:
        await make_preset(
            conn, id=s["id"], code=s["code"],
            embedding_profile=settings.embedding_profile,
            visibility="PUBLIC",
            embedding=deterministic_vector(s.get("vec", s["code"])),
        )
    cache = PresetCache()
    cache.load(await keyword_preset_repo.load_active(conn, settings.embedding_profile))
    return cache


async def _run(db, conn, settings, llm, n):
    """Context 1건을 판정까지 흘린다. n 은 설정 사본으로 주입한다 — 환경변수를 건드리면
    session 범위 `settings` fixture 가 다른 테스트로 샌다."""
    cache = await _load_cache(
        conn, settings,
        [
            {"id": 101, "code": "F", "vec": "친구랑 저녁"},
            {"id": 102, "code": "G", "vec": "친구랑 저녁"},
        ],
    )
    await make_state(conn, context_id=1, embedding_status="COMPLETED", keyword_status="PENDING")
    await make_embedding(
        conn, context_id=1, user_id=1, record_id=1,
        embedding_profile=settings.embedding_profile,
        embedding=deterministic_vector("친구랑 저녁"),
    )
    voted = settings.model_copy(update={"judge_vote_n": n})
    proc = ContextProcessingService(
        db,
        EmbeddingService(db, FakeEmbeddingClient(), voted),
        KeywordService(db, llm, cache, voted),
    )
    await proc.process(
        ContextProcessRequest(contextId=1, userId=1, recordId=1, text="친구랑 저녁")
    )
    return conn


async def _state(conn, cid):
    return await conn.fetchrow(
        "SELECT embedding_status, keyword_status FROM ai.context_ai_state WHERE context_id=$1",
        cid,
    )


async def _kw(conn, cid):
    return [
        r["keyword_id"]
        for r in await conn.fetch(
            "SELECT keyword_id FROM ai.context_keyword WHERE context_id=$1 ORDER BY keyword_id",
            cid,
        )
    ]


async def test_n1_calls_the_model_exactly_once(db, conn, settings):
    """되돌린 상태에서 호출이 늘지 않는다 — 이 티켓의 롤백 계약이다."""
    llm = SequenceLLMClient([_res([(101, 0.9)])])
    await _run(db, conn, settings, llm, 1)
    assert llm.call_count == 1
    assert await _kw(conn, 1) == [101]


async def test_n3_calls_the_model_three_times_and_votes(db, conn, settings):
    llm = SequenceLLMClient(
        [_res([(101, 0.9), (102, 0.6)]), _res([(101, 0.8)]), _res([(101, 0.7)])]
    )
    await _run(db, conn, settings, llm, 3)
    assert llm.call_count == 3
    # 102 는 1/3 이라 떨어지고 101 만 남는다 — 흔들리는 것이 지워지는 경로 그 자체.
    assert await _kw(conn, 1) == [101]
    assert (await _state(conn, 1))["keyword_status"] == "COMPLETED"


async def test_quorum_met_despite_one_failure_still_persists(db, conn, settings):
    """3회 중 1회 실패는 정족수를 넘긴다 — 남은 2표로 판정한다."""
    llm = SequenceLLMClient(
        [_res([(101, 0.9)]), TransientError("llm error: 503"), _res([(101, 0.8)])]
    )
    await _run(db, conn, settings, llm, 3)
    assert await _kw(conn, 1) == [101]
    assert (await _state(conn, 1))["keyword_status"] == "COMPLETED"


async def test_quorum_missed_keeps_processing_for_rescan(db, conn, settings):
    """3회 중 2회 실패 → 정족수 미달. **선택 0건으로 저장하지 않는다.**

    이 단언이 이 티켓에서 가장 중요하다. 분모가 n 이라 정족수 미달로 다수결을 돌리면
    아무 키워드도 과반을 못 받아 「선택 0건 정상 완료」가 되는데, 그것은 판정 실패를
    성공으로 기록하는 것이다.
    """
    llm = SequenceLLMClient(
        [_res([(101, 0.9)]), TransientError("llm error: 503"), TransientError("llm error: 503")]
    )
    await _run(db, conn, settings, llm, 3)
    st = await _state(conn, 1)
    assert st["keyword_status"] == "PROCESSING"     # 재스캔이 회수한다
    assert st["embedding_status"] == "COMPLETED"
    assert await _kw(conn, 1) == []


async def test_quorum_missed_all_permanent_fails_the_stage(db, conn, settings):
    llm = SequenceLLMClient([PermanentError("llm error: 401")] * 3)
    await _run(db, conn, settings, llm, 3)
    st = await _state(conn, 1)
    assert st["keyword_status"] == "FAILED"
    assert st["embedding_status"] == "COMPLETED"


async def test_mixed_failures_prefer_transient_so_the_context_is_recoverable(db, conn, settings):
    """permanent 와 transient 가 섞이면 되돌릴 수 있는 쪽(transient)으로 올린다."""
    llm = SequenceLLMClient(
        [
            PermanentError("llm error: 401"),
            TransientError("llm error: 503"),
            PermanentError("llm error: 401"),
        ]
    )
    await _run(db, conn, settings, llm, 3)
    assert (await _state(conn, 1))["keyword_status"] == "PROCESSING"

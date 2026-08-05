"""Keyword fusion 순수 로직 검증 (P48 1단계).

**DB 도 GMS 도 부르지 않는다.** `tools/search_cut/fusion.py` 는 인자만 받는 순수 함수라
픽스처로 전부 검증된다 — 실데이터가 없어도 **조인·NULL·visibility·상태·집계·결합식**이
맞는지는 지금 확정할 수 있다.

여기서 쓰는 값은 **가짜다.** 검색이 좋아졌는지는 이 파일이 답하지 않는다(실측은
`.search/` 의 행렬만 쓴다). 이 파일이 답하는 것은 **계산이 규칙대로인가** 하나다.
그 둘을 섞지 않는 것이 `word_matrix.py` 가 포트 가드를 둔 이유와 같다.

`pytest` 로도 돌고, venv 가 없는 환경에서 직접 돌려도 된다.

    python tests/test_search_fusion.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "search_cut"))

import fusion as F  # noqa: E402

# ── 픽스처 ───────────────────────────────────────────────────────────────────
#
# Preset 4종으로 세 갈래를 모두 덮는다. `BLOCKED` 가 실 시드에 없더라도 **낡은 데이터에
# 남아 있을 가능성을 방어**하는 것이 P48 §1-c 의 요구다.
PRESETS = {
    1: F.Preset(id=1, version=1, visibility="PUBLIC"),
    2: F.Preset(id=2, version=1, visibility="PRIVATE_ONLY"),
    3: F.Preset(id=3, version=1, visibility="BLOCKED"),
    4: F.Preset(id=4, version=1, visibility="PUBLIC"),
}

QUERY_COS = {1: 0.42, 2: 0.31, 3: 0.55, 4: 0.12}


def ctx(cid, rid, kws, status="COMPLETED"):
    return F.ContextKeywords(
        context_id=cid, record_id=rid, keyword_status=status, keywords=tuple(kws)
    )


# ── 1. query → Preset 후보 ───────────────────────────────────────────────────

def test_blocked_preset_is_never_a_candidate():
    """`BLOCKED` 는 코사인이 1위여도 후보가 아니다 (keyword-preset.md §2)."""
    cand = F.preset_candidates(QUERY_COS, PRESETS, top_k=10, floor=0.0)
    ids = [pid for pid, _ in cand]
    assert 3 not in ids, "BLOCKED(3)이 후보에 들어왔다 — 코사인 0.55로 1위인데도 빠져야 한다"
    assert ids[0] == 1, "BLOCKED를 뺀 뒤 1위는 preset 1(0.42)이어야 한다"


def test_private_only_is_usable():
    """`PRIVATE_ONLY` 는 개인 검색 신호로 쓴다 (P48 §1-c)."""
    cand = F.preset_candidates(QUERY_COS, PRESETS, top_k=10, floor=0.0)
    assert 2 in [pid for pid, _ in cand]


def test_floor_applies_before_top_k():
    """하한이 먼저다 — 반대면 후보가 모자랄 때 하한 아래가 채워진다."""
    cand = F.preset_candidates(QUERY_COS, PRESETS, top_k=10, floor=0.35)
    assert [pid for pid, _ in cand] == [1], "0.35 이상은 preset 1 뿐이다"


def test_candidates_are_deterministic_on_ties():
    tied = {1: 0.5, 2: 0.5, 4: 0.5}
    a = F.preset_candidates(tied, PRESETS, top_k=2, floor=0.0)
    b = F.preset_candidates(tied, PRESETS, top_k=2, floor=0.0)
    assert a == b == [(1, 0.5), (2, 0.5)], "동점은 preset_id 오름차순으로 깨야 한다"


# ── 2. keyword_status — Context 제외와 신호 제외의 구분 ──────────────────────

def test_incomplete_status_removes_signal_only():
    """`COMPLETED` 가 아니면 신호가 0이다. **Context 를 후보에서 빼지는 않는다**(§1-b)."""
    cand = F.preset_candidates(QUERY_COS, PRESETS, top_k=10, floor=0.0)
    for status in ("PENDING", "PROCESSING", "FAILED", "CANCELLED"):
        c = ctx(10, 100, [(1, 0.9)], status=status)
        assert F.context_signal(c, cand, PRESETS, method=F.BINARY) == 0.0, status

    # 그 Record 는 벡터 후보로 여전히 살아 있어야 한다 — 사라지면 명백한 퇴행이다.
    rows = [{"record_id": 100, "sim": 0.31, "is_expected": True}]
    fused = F.fuse(rows, {}, method=F.CONFIDENCE, weight=0.2, limit=20)
    assert [r["record_id"] for r in fused] == [100]
    assert fused[0]["sim"] == 0.31, "벡터 점수가 유지돼야 한다"


# ── 3. confidence = NULL (P48 §1-d) ──────────────────────────────────────────

def test_null_confidence_does_not_kill_binary_match():
    """binary 는 confidence 를 쓰지 않으므로 NULL 이 match 를 없애지 않는다."""
    cand = F.preset_candidates(QUERY_COS, PRESETS, top_k=10, floor=0.0)
    c = ctx(10, 100, [(1, None)])
    assert F.context_signal(c, cand, PRESETS, method=F.BINARY) == 1.0


def test_null_policies_differ_and_none_is_zero():
    """세 정책이 서로 다른 값을 내고, **어느 것도 0 이 아니다**.

    0 이 나오면 「판정된 적 없음」과 구분이 사라진다 — P48 이 금지한 처리다.
    """
    cand = F.preset_candidates(QUERY_COS, PRESETS, top_k=10, floor=0.0)
    c = ctx(10, 100, [(1, None)])
    vals = {
        p: F.context_signal(
            c, cand, PRESETS, method=F.CONFIDENCE, null_policy=p, null_fill=0.5
        )
        for p in (F.NULL_INCLUDE, F.NULL_FILL)
    }
    assert vals[F.NULL_INCLUDE] == 0.42, "include 는 confidence 를 1.0 으로 본다"
    assert vals[F.NULL_FILL] == 0.42 * 0.5
    assert all(v > 0 for v in vals.values()), "NULL 이 0 으로 뭉개지면 안 된다"


def test_null_exclude_skips_that_keyword_only():
    """`exclude` 는 그 keyword 만 빼고 다른 keyword 는 살린다."""
    cand = F.preset_candidates(QUERY_COS, PRESETS, top_k=10, floor=0.0)
    c = ctx(10, 100, [(1, None), (2, 0.8)])
    got = F.context_signal(
        c, cand, PRESETS, method=F.CONFIDENCE, null_policy=F.NULL_EXCLUDE
    )
    assert got == 0.31 * 0.8, "preset 1(NULL)은 빠지고 preset 2 만 남아야 한다"

    only_null = ctx(11, 101, [(1, None)])
    assert F.context_signal(
        only_null, cand, PRESETS, method=F.CONFIDENCE, null_policy=F.NULL_EXCLUDE
    ) == 0.0


# ── 4. Record 집계 = max (P48 §1-a) ──────────────────────────────────────────

def test_record_signal_is_max_over_contexts():
    """평균·합계가 아니라 최댓값 — 하나만 강하게 맞아도 그 Record 는 찾는 대상이다."""
    cand = F.preset_candidates(QUERY_COS, PRESETS, top_k=10, floor=0.0)
    contexts = [
        ctx(1, 500, [(1, 0.2)]),   # 0.42 * 0.2 = 0.084
        ctx(2, 500, [(1, 0.9)]),   # 0.42 * 0.9 = 0.378  ← 최댓값
        ctx(3, 500, []),
    ]
    sig = F.record_signals(contexts, cand, PRESETS, method=F.CONFIDENCE)
    assert abs(sig[500] - 0.378) < 1e-9
    mean = (0.084 + 0.378 + 0) / 3
    assert abs(sig[500] - mean) > 1e-6, "평균으로 계산하면 안 된다"


# ── 5. 합집합 → 정렬 → limit (P48 §2.1) ──────────────────────────────────────

def test_keyword_signal_without_cosine_is_an_error():
    """코사인 없는 후보를 만들지 않는다 — `similarity` 는 cosine float 계약이다(P48 §2.3).

    이런 행이 나오는 것은 두 모집단이 어긋났다는 뜻이고, 원인은 대개 `keyword_matrix` 가
    `embedding_status='COMPLETED'` 를 안 건 것이다. **지어내지 않고 실패시킨다.**
    """
    rows = [{"record_id": 1, "sim": 0.40, "is_expected": False}]
    try:
        F.fuse(rows, {99: 1.0}, method=F.CONFIDENCE, weight=0.5, limit=20)
        raise AssertionError("코사인 없는 keyword 후보가 통과했다")
    except F.FusionError as exc:
        assert "embedding_status" in str(exc), "원인을 짚는 메시지여야 한다"


def test_no_row_ever_carries_none_sim():
    """어떤 경로로도 `sim=None` 행이 나오지 않는다."""
    rows = [{"record_id": i, "sim": 0.5 - i * 0.01, "is_expected": False} for i in range(1, 4)]
    for method in (F.BINARY, F.CONFIDENCE, F.IDF, F.RRF):
        fused = F.fuse(rows, {2: 1.0}, method=method, weight=0.3, limit=20)
        assert all(isinstance(r["sim"], float) for r in fused), method


def test_limit_applies_after_union_and_sort():
    """벡터 하위였던 Record 가 신호로 1위에 오고, `limit` 은 그 뒤에 걸린다."""
    rows = [{"record_id": i, "sim": 0.5 - i * 0.01, "is_expected": False} for i in range(1, 6)]
    fused = F.fuse(rows, {5: 1.0}, method=F.CONFIDENCE, weight=10.0, limit=3)
    assert len(fused) == 3
    assert fused[0]["record_id"] == 5, "가중치가 크면 신호 있는 Record 가 1위여야 한다"
    assert [r["rank"] for r in fused] == [1, 2, 3]


def test_fusion_score_never_overwrites_sim():
    """응답의 `similarity` 는 기존 코사인 의미를 유지한다(P48 §2.3)."""
    rows = [{"record_id": 1, "sim": 0.31, "is_expected": True}]
    fused = F.fuse(rows, {1: 1.0}, method=F.CONFIDENCE, weight=0.5, limit=20)
    assert fused[0]["sim"] == 0.31
    assert fused[0]["fusion"] != fused[0]["sim"], "fusion 은 별도 필드여야 한다"


# ── 6. 컷은 방식마다 다르다 (P48 §2.2) ───────────────────────────────────────

def test_rrf_does_not_apply_cosine_tau_to_fusion_score():
    """RRF 점수에 코사인 `tau=0.30` 을 걸면 전부 잘린다 — 그래서 걸지 않는다."""
    rows = [{"record_id": 1, "sim": 0.45, "is_expected": True}]
    fused = F.fuse(rows, {1: 1.0}, method=F.RRF, limit=20)
    assert fused[0]["fusion"] < 0.30, "RRF 점수는 코사인 스케일보다 훨씬 작다(전제 확인)"

    kept = F.apply_cut(fused, method=F.RRF, tau=0.30, ratio=0.60)
    assert len(kept) == 1, "RRF 인데 코사인 tau 로 잘렸다"


def test_weighted_sum_cut_uses_fusion_score():
    """가중합은 fusion 점수에 컷을 건다 — 그래서 컷 재측정이 완료 조건이다."""
    rows = [
        {"record_id": 1, "sim": 0.50, "is_expected": True},
        {"record_id": 2, "sim": 0.20, "is_expected": False},
    ]
    fused = F.fuse(rows, {}, method=F.CONFIDENCE, weight=0.0, limit=20)
    kept = F.apply_cut(fused, method=F.CONFIDENCE, tau=0.30, ratio=0.60)
    assert [r["record_id"] for r in kept] == [1]


def test_rrf_applies_cosine_eligibility_gate():
    """RRF 라도 **무관한 Record 는 코사인 관문에서 걸러야 한다** — 자동 통과가 없다."""
    rows = [
        {"record_id": 1, "sim": 0.45, "is_expected": True},
        {"record_id": 2, "sim": 0.12, "is_expected": False},   # 절대 하한 아래
    ]
    fused = F.fuse(rows, {2: 1.0}, method=F.RRF, limit=20)
    kept = F.apply_cut(fused, method=F.RRF, tau=0.30, ratio=0.60)
    assert [r["record_id"] for r in kept] == [1], \
        "코사인 0.12 인 Record 가 keyword 신호만으로 통과했다"


def test_cutoff_zero_does_not_disable_cosine_gate():
    """`rrf_cutoff=0` 은 **비활성일 뿐 cosine eligibility 를 없애지 않는다**.

    「cutoff 를 껐으니 아무거나 통과」가 되면 무관 질의 침묵이 무너진다.
    """
    rows = [
        {"record_id": 1, "sim": 0.45, "is_expected": True},
        {"record_id": 2, "sim": 0.10, "is_expected": False},
    ]
    fused = F.fuse(rows, {2: 1.0}, method=F.RRF, limit=20)
    kept = F.apply_cut(fused, method=F.RRF, tau=0.30, ratio=0.60, rrf_cutoff=0.0)
    assert [r["record_id"] for r in kept] == [1], \
        "cutoff=0 이 코사인 관문까지 끈다 — 비활성과 면제는 다르다"


def test_rrf_k_changes_score_scale():
    """`rrf_k` 가 바뀌면 점수 스케일이 바뀐다 — 그래서 cutoff 를 다시 재야 한다.

    이 사실이 코드로 고정돼 있지 않으면 `rrf_k` 를 바꾼 뒤 낡은 cutoff 를 그대로 쓰게 된다.
    """
    rows = [{"record_id": 1, "sim": 0.45, "is_expected": True}]
    a = F.fuse(rows, {1: 1.0}, method=F.RRF, limit=20, rrf_k=60.0)[0]["fusion"]
    b = F.fuse(rows, {1: 1.0}, method=F.RRF, limit=20, rrf_k=10.0)[0]["fusion"]
    assert a != b, "rrf_k 가 점수에 영향을 주지 않는다 — 전제가 틀렸다"
    assert b > a, "k 가 작을수록 점수가 커야 한다(1/(k+rank))"


def test_rrf_cutoff_is_a_separate_gate():
    """`rrf_cutoff` 는 코사인 관문과 별개다 — 어느 신호로도 위로 못 온 행을 막는다."""
    rows = [{"record_id": i, "sim": 0.50, "is_expected": False} for i in range(1, 4)]
    fused = F.fuse(rows, {}, method=F.RRF, limit=20)
    assert len(F.apply_cut(fused, method=F.RRF, tau=0.30, ratio=0.60, rrf_cutoff=0.0)) == 3
    high = max(r["fusion"] for r in fused)
    kept = F.apply_cut(fused, method=F.RRF, tau=0.30, ratio=0.60, rrf_cutoff=high)
    assert len(kept) == 1, "cutoff 가 fusion 점수에 걸려야 한다"


# ── 7. IDF (참고용) ──────────────────────────────────────────────────────────

def test_idf_gives_rare_keyword_more_weight():
    contexts = [ctx(i, i, [(1, 0.5)]) for i in range(1, 5)] + [ctx(9, 9, [(4, 0.5)])]
    idf = F.idf_weights(contexts, PRESETS)
    assert idf[4] > idf[1], "희소한 keyword 의 IDF 가 커야 한다"


def test_idf_counts_records_not_contexts():
    """문서 단위는 **Record** 다 — Context 로 세면 Context 가 많은 Record 가 df 를 부풀린다.

    아래에서 keyword 1 은 Record 2개에, keyword 4 도 Record 2개에 있다. Context 로 세면
    1 이 4건·4 가 2건이라 IDF 가 갈리지만, Record 로 세면 같아야 한다.
    """
    contexts = [
        ctx(1, 100, [(1, 0.5)]), ctx(2, 100, [(1, 0.5)]), ctx(3, 100, [(1, 0.5)]),
        ctx(4, 200, [(1, 0.5)]),
        ctx(5, 300, [(4, 0.5)]), ctx(6, 400, [(4, 0.5)]),
    ]
    idf = F.idf_weights(contexts, PRESETS)
    assert abs(idf[1] - idf[4]) < 1e-9, \
        f"Record 기준이면 같아야 한다 (1={idf[1]}, 4={idf[4]}) — Context 로 세고 있다"


def test_idf_excludes_incomplete_status_and_blocked():
    """모집단이 신호와 같아야 한다 — `COMPLETED` 만, `BLOCKED` 제외(P48 §1-b·§1-c)."""
    contexts = [
        ctx(1, 100, [(1, 0.5), (3, 0.5)]),                    # 3 은 BLOCKED
        ctx(2, 200, [(1, 0.5)], status="PROCESSING"),          # 신호 없음
        ctx(3, 300, [(4, 0.5)]),
    ]
    idf = F.idf_weights(contexts, PRESETS)
    assert 3 not in idf, "BLOCKED preset 이 IDF 에 들어갔다"
    # 분모 N 은 COMPLETED Record 2개(100·300). keyword 1 은 그중 1개에만 있다.
    import math
    assert abs(idf[1] - math.log(2 / 1)) < 1e-9, \
        "PROCESSING Context 의 Record 가 분모·분자에 섞였다"


# ── 8. 가드 ──────────────────────────────────────────────────────────────────

def test_keyword_matrix_population_matches_search_query():
    """`keyword_matrix` 의 Context 모집단이 검색 Query 와 같아야 한다.

    두 상태의 역할이 다르다 — 이것이 「코사인 없는 후보」 결함의 뿌리였으므로 회귀를 막는다.

        embedding_status = COMPLETED   **검색 후보 자체의 조건.** WHERE 에 있어야 한다
        keyword_status                 **신호의 조건일 뿐.** WHERE 에 있으면 퇴행이다(§1-b)

    소스를 읽어 검사한다 — `keyword_matrix` 는 `app.*` 를 import 하므로 의존성 없이
    불러올 수 없고, 이 검사에 필요한 것은 SQL 문자열뿐이다.
    """
    src = (Path(__file__).resolve().parents[1]
           / "tools" / "search_cut" / "keyword_matrix.py").read_text(encoding="utf-8")
    body = src.split("_CONTEXTS = ", 1)[1].split('"""', 2)[1]
    assert "embedding_status = 'COMPLETED'" in body, \
        "검색 후보 조건이 빠졌다 — 벡터 행렬에 없는 Record 가 신호로만 올라온다"
    where = body.split("WHERE", 1)[1]
    assert "keyword_status" not in where, \
        "keyword_status 가 WHERE 에 들어갔다 — Context 제외는 퇴행이다(§1-b)"
    assert "s.keyword_status" in body.split("WHERE", 1)[0], \
        "keyword_status 는 신호 판단용으로 컬럼에 실려 있어야 한다"


def test_unknown_method_and_policy_raise():
    cand = F.preset_candidates(QUERY_COS, PRESETS, top_k=10, floor=0.0)
    try:
        F.fuse([], {}, method="nope", limit=20)
        raise AssertionError("알 수 없는 방식이 통과했다")
    except F.FusionError:
        pass
    try:
        F.context_signal(
            ctx(1, 1, [(1, None)]), cand, PRESETS,
            method=F.CONFIDENCE, null_policy="nope",
        )
        raise AssertionError("알 수 없는 NULL 정책이 통과했다")
    except F.FusionError:
        pass


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}\n        {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} 통과")
    raise SystemExit(1 if failed else 0)

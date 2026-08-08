"""Keyword 신호 fusion — 순수 로직 (P48 1단계).

**이 모듈은 DB 도 GMS 도 파일도 읽지 않는다.** 입력은 전부 인자로 받는다. `fusion_sweep.py`
가 artifact 를 읽어 여기에 넘기고, `tests/test_search_fusion.py` 가 픽스처로 같은 함수를
부른다 — 실데이터가 없어도 **조인·NULL·visibility·집계·결합식이 맞는지는 지금 검증된다.**

계산 규칙의 정본은 [P48](../../docs/proposals/P48-search-signal-expansion.md) §1·§2 다.
여기서는 그 규칙을 코드로 옮기고, 어긋나기 쉬운 곳에 이유를 적는다.

## 옮긴 규칙

    §2.1  합집합이 먼저다        벡터 상위 limit 안에서 재정렬하지 않는다.
                                 벡터 후보 ∪ keyword 후보 → 정렬 → limit
    §1-a  Record 집계는 max      벡터가 DISTINCT ON 으로 최댓값을 쓰는 것과 같은 규칙.
                                 두 신호가 서로 다른 Context 에서 최댓값을 받아도 된다
    §1-b  Context 제외 ≠ 신호 제외  keyword_status != COMPLETED 는 **신호만** 없앤다.
                                 벡터 점수는 유지된다. 제외하면 명백한 퇴행이다
    §1-c  BLOCKED 만 뺀다         PUBLIC · PRIVATE_ONLY 는 쓴다(keyword-preset.md §2·§6)
    §1-d  NULL 은 0 이 아니다     0 으로 치환하면 「판정된 적 없음」과 구분이 사라진다

## 이 모듈이 정하지 않는 것

`top_k` · `floor` · `weight` · NULL 정책의 **값**은 정하지 않는다. sweep 의 조절 축이므로
전부 인자다. 기본값은 실험의 출발점일 뿐 채택값이 아니다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ── NULL confidence 정책 (P48 §1-d) ──────────────────────────────────────────
#
# **0 으로 치환하는 정책은 없다.** 그것이 이 세 갈래를 두는 이유다.
NULL_INCLUDE = "include"  # 판정은 됐으므로 확신도를 최대(1.0)로 본다
NULL_EXCLUDE = "exclude"  # 그 keyword 를 confidence 계산에서 뺀다 (match 자체는 살아 있다)
NULL_FILL = "fill"        # 고정 대체값을 쓴다 (null_fill 인자)

NULL_POLICIES = (NULL_INCLUDE, NULL_EXCLUDE, NULL_FILL)

# ── fusion 방식 (P48 §1-e) ───────────────────────────────────────────────────
BINARY = "binary"        # keyword 가 맞으면 고정 보너스. confidence 를 쓰지 않는다
CONFIDENCE = "confidence"  # query-preset cosine × context confidence
IDF = "idf"              # confidence 에 IDF 감쇠. **참고용** — Record 42건에서 df 가 불안정하다
RRF = "rrf"              # 순위 기반. 점수 스케일이 무관하다

METHODS = (BINARY, CONFIDENCE, IDF, RRF)

BLOCKED = "BLOCKED"


class FusionError(Exception):
    """계산을 진행하면 안 되는 상태. 값을 지어내지 않고 멈춘다."""


@dataclass(frozen=True)
class Preset:
    id: int
    version: int
    visibility: str

    @property
    def usable(self) -> bool:
        """`BLOCKED` 만 뺀다 — `PUBLIC` · `PRIVATE_ONLY` 는 쓴다(P48 §1-c).

        개인 검색은 본인 Context 만 대상이고 Keyword 를 응답에 노출하지 않으므로
        `PRIVATE_ONLY` 는 공개 범위 판단과 충돌하지 않는다(keyword-preset.md §6).
        """
        return self.visibility != BLOCKED


@dataclass(frozen=True)
class ContextKeywords:
    context_id: int
    record_id: int
    keyword_status: str
    # (keyword_id, confidence|None). **NULL 을 그대로 보존한다.**
    keywords: tuple[tuple[int, float | None], ...] = field(default=())

    @property
    def signal_usable(self) -> bool:
        """`COMPLETED` 일 때만 keyword 신호를 쓴다.

        **Context 를 후보에서 빼는 것이 아니다**(P48 §1-b). 재판정 중이거나 무효화된
        판정(`PROCESSING`·`CANCELLED`·`FAILED`)이 신호로 살아나면 안 될 뿐이고,
        그 Context 의 벡터 점수는 그대로 남는다.
        """
        return self.keyword_status == "COMPLETED"


# ── 1. query → Preset 후보 ───────────────────────────────────────────────────

def preset_candidates(
    query_cos: dict[int, float],
    presets: dict[int, Preset],
    *,
    top_k: int,
    floor: float,
) -> list[tuple[int, float]]:
    """질의-Preset 코사인에서 후보를 고른다. **추가 모델 호출이 없다**(P48 1단계).

    `floor` 를 top_k 보다 먼저 건다 — 반대로 하면 후보가 부족할 때 하한 아래가 채워진다.
    동점은 `preset_id` 오름차순으로 깨어 결과를 결정적으로 만든다.
    """
    if top_k <= 0:
        return []
    usable = [
        (pid, cos) for pid, cos in query_cos.items()
        if pid in presets and presets[pid].usable and cos >= floor
    ]
    usable.sort(key=lambda x: (-x[1], x[0]))
    return usable[:top_k]


# ── 2. Context 단위 keyword 신호 ─────────────────────────────────────────────

def _confidence_of(
    raw: float | None, *, null_policy: str, null_fill: float
) -> float | None:
    """NULL 정책을 적용한 확신도. `None` 이면 그 keyword 를 계산에서 뺀다는 뜻이다."""
    if raw is not None:
        return raw
    if null_policy == NULL_INCLUDE:
        return 1.0
    if null_policy == NULL_FILL:
        return null_fill
    if null_policy == NULL_EXCLUDE:
        return None
    raise FusionError(f"알 수 없는 NULL 정책: {null_policy}")


def context_signal(
    ctx: ContextKeywords,
    candidates: list[tuple[int, float]],
    presets: dict[int, Preset],
    *,
    method: str,
    null_policy: str = NULL_INCLUDE,
    null_fill: float = 0.5,
    idf: dict[int, float] | None = None,
) -> float:
    """Context 하나의 keyword 신호. 0.0 이면 신호 없음.

    `binary` 는 `confidence` 를 **쓰지 않는다**(P48 §1-d) — 그래서 NULL 정책과 무관하게
    match 만으로 값이 정해진다. NULL 이 match 를 없애지 않는다는 규칙이 여기서 보장된다.
    """
    if not ctx.signal_usable or not candidates:
        return 0.0

    have = {kid: conf for kid, conf in ctx.keywords
            if kid in presets and presets[kid].usable}
    if not have:
        return 0.0

    hits = [(pid, qcos) for pid, qcos in candidates if pid in have]
    if not hits:
        return 0.0

    if method == BINARY:
        return 1.0

    if method in (CONFIDENCE, IDF):
        scores = []
        for pid, qcos in hits:
            conf = _confidence_of(have[pid], null_policy=null_policy, null_fill=null_fill)
            if conf is None:      # NULL_EXCLUDE — 이 keyword 는 세지 않는다
                continue
            s = qcos * conf
            if method == IDF:
                s *= (idf or {}).get(pid, 1.0)
            scores.append(s)
        return max(scores) if scores else 0.0

    if method == RRF:
        # RRF 는 점수가 아니라 순위를 쓴다. 여기서는 「가장 잘 맞은 후보의 순위」를 돌려주고
        # 실제 RRF 합산은 fuse() 가 한다 — 순위는 질의 단위 정렬에서만 정의되기 때문이다.
        best = min(i for i, (pid, _) in enumerate(candidates, 1) if pid in have)
        return 1.0 / best

    raise FusionError(f"알 수 없는 fusion 방식: {method}")


# ── 3. Record 집계 — max (P48 §1-a) ──────────────────────────────────────────

def record_signals(
    contexts: list[ContextKeywords],
    candidates: list[tuple[int, float]],
    presets: dict[int, Preset],
    **kw,
) -> dict[int, float]:
    """Record 별 keyword 신호 = 소속 Context 중 **최댓값**.

    평균·합계를 쓰지 않는다 — 벡터가 `DISTINCT ON` 으로 최댓값을 고르는 것과 같은 규칙이다
    (personal-search.md §5: Context 는 서로 독립적인 저장 이유이므로 하나만 강하게 일치해도
    그 Record 는 찾는 대상이다).
    """
    out: dict[int, float] = {}
    for ctx in contexts:
        s = context_signal(ctx, candidates, presets, **kw)
        if s > 0:
            out[ctx.record_id] = max(out.get(ctx.record_id, 0.0), s)
    return out


# ── 4. 합집합 → 정렬 → limit (P48 §2.1) ──────────────────────────────────────

def _rrf(rank: int, k: float) -> float:
    return 1.0 / (k + rank)


def fuse(
    vector_rows: list[dict],
    keyword_signal: dict[int, float],
    *,
    method: str,
    weight: float = 0.0,
    limit: int = 20,
    rrf_k: float = 60.0,
) -> list[dict]:
    """벡터 후보와 keyword 후보의 **합집합**을 만든 뒤 정렬하고 마지막에 `limit`.

    **벡터 상위 `limit` 안에서만 재정렬하지 않는다**(P48 §2.1). 목적이 벡터 순위 밖의
    정답을 끌어올리는 것인데 재정렬만 하면 그 정답이 후보에 없어 구조적으로 불가능하다.

    `vector_rows` 는 유사도 내림차순이어야 한다(행렬이 그렇게 저장한다).

    반환 행은 `sim`(코사인 원값)을 **그대로 보존한다** — 응답의 `similarity` 는 기존 코사인
    의미를 유지해야 하고, `fusion` 점수는 정렬에만 쓰며 API 에 노출하지 않는다(P48 §2.3).

    **모든 후보에 실제 코사인이 있어야 한다.** `similarity` 는 cosine float 계약이므로
    `None` 을 허용하지 않는다. keyword 신호가 있는데 코사인이 없는 Record 는 **지어내지
    않고 실패시킨다** — 그런 행이 나오는 것은 두 모집단이 어긋났다는 뜻이고, 원인은 대개
    `keyword_matrix` 가 `embedding_status = 'COMPLETED'` 를 걸지 않은 것이다. 검색 Query 는
    그 조건을 걸므로 미완료 Context 는 **검색 후보 자체가 아니다**(신호만 빠지는
    `keyword_status` 와 다르다).

    오프라인에서 `vector_rows` 는 소유자 Record 전량이므로(행렬이 `NO_LIMIT` 으로 뜬다)
    합집합은 벡터 후보 집합과 같아진다. 런타임에서 `LIMIT` 뒤에 합치려면 keyword 후보의
    코사인을 **조회해서 채운 뒤** 이 함수에 넘겨야 한다 — 그것이 §2.1 이 요구하는 순서다.
    """
    if method not in METHODS:
        raise FusionError(f"알 수 없는 fusion 방식: {method}")

    by_record = {r["record_id"]: r for r in vector_rows}
    vec_rank = {r["record_id"]: i for i, r in enumerate(vector_rows, 1)}

    orphan = sorted(rid for rid, s in keyword_signal.items()
                    if s > 0 and rid not in by_record)
    if orphan:
        raise FusionError(
            f"keyword 신호가 있는데 코사인이 없는 Record: {orphan}\n"
            "  `similarity` 는 cosine float 계약이라 None 을 넣을 수 없다(P48 §2.3).\n"
            "  keyword_matrix 의 Context 모집단에 embedding_status='COMPLETED' 가 "
            "걸렸는지 확인한다 — 미완료 Context 는 검색 후보 자체가 아니다."
        )

    union = list(by_record.keys())

    kw_ranked = sorted(keyword_signal.items(), key=lambda x: (-x[1], x[0]))
    kw_rank = {rid: i for i, (rid, _) in enumerate(kw_ranked, 1)}

    out = []
    for rid in union:
        base = by_record[rid]
        sim = float(base["sim"])
        ksig = keyword_signal.get(rid, 0.0)

        if method == RRF:
            score = 0.0
            if rid in vec_rank:
                score += _rrf(vec_rank[rid], rrf_k)
            if rid in kw_rank and ksig > 0:
                score += _rrf(kw_rank[rid], rrf_k)
        else:
            # 가중합. 코사인 스케일을 유지하므로 컷을 **재측정**해야 한다(P48 §2.2).
            score = (sim if sim is not None else 0.0) + weight * ksig

        row = dict(base)
        row["fusion"] = score
        row["keyword_signal"] = ksig
        out.append(row)

    # 동점은 원래 벡터 순위 → record_id 로 깨어 결정적으로 만든다.
    out.sort(key=lambda r: (-r["fusion"], vec_rank.get(r["record_id"], 10**9), r["record_id"]))
    for i, r in enumerate(out[:limit], 1):
        r["rank"] = i
    return out[:limit]


# ── 4-b. 재정렬 전용 병합 (P49 §4) ───────────────────────────────────────────


def rerank(
    kept_rows: list[dict],
    keyword_signal: dict[int, float],
    *,
    method: str,
    weight: float = 0.0,
    rrf_k: float = 60.0,
) -> list[dict]:
    """벡터 컷 통과 집합의 **순서만** 바꾼다 — 후보 추가·제거 없음 (P49 §4).

    `fuse()` 와 다른 병합 의미다. `fuse()` 는 P48 구조(후보 합집합을 만든 뒤 병합
    점수에 컷)를 구현하고, 이 함수는 P49 가 확정한 구조(코사인 컷이 후보를 먼저
    확정하고 keyword 신호는 그 안의 순서만 조정)를 구현한다. 이 구조에서는 관련
    없는 질의의 벡터 후보가 0건이면 재정렬 대상도 0건이므로, keyword 신호만으로
    관련 없는 결과가 새로 노출될 수 없다(P49 §5).

    `kept_rows` 는 컷을 통과한 행을 유사도 내림차순으로 받는다. 재정렬 뒤 두 번째
    후보 절단을 하지 않는다 — 대상이 이미 `limit` 이하라 절단할 것이 없고, 절단을
    더하면 후보 집합 불변 계약이 깨진다.

    정렬 규칙은 방식별로 다음과 같다.

        binary·confidence·idf   정렬 점수 = 원래 코사인 + weight × 신호.
                                점수는 정렬에만 쓰고 행의 `sim` 은 그대로 둔다
        rrf                     정렬 점수 = 1/(k+벡터순위) + 1/(k+keyword순위).
                                keyword 순위는 신호가 있는 행끼리 신호 내림차순

    동점은 원래 벡터 순위로 깨어 결정적으로 만든다 — 신호가 없는 행끼리는 벡터
    순서가 그대로 유지된다.

    반환 행의 record_id 집합이 입력과 다르면 구현 오류이므로 `FusionError` 로
    멈춘다. 조용히 다른 집합을 돌려주는 것보다 실패가 낫다(가드 선례).
    """
    if method not in METHODS:
        raise FusionError(f"알 수 없는 fusion 방식: {method}")
    if not kept_rows:
        return []

    vec_rank = {r["record_id"]: i for i, r in enumerate(kept_rows, 1)}

    if method == RRF:
        with_signal = sorted(
            (rid for rid in vec_rank if keyword_signal.get(rid, 0.0) > 0),
            key=lambda rid: (-keyword_signal[rid], rid),
        )
        kw_rank = {rid: i for i, rid in enumerate(with_signal, 1)}

        def score(row: dict) -> float:
            rid = row["record_id"]
            s = _rrf(vec_rank[rid], rrf_k)
            if rid in kw_rank:
                s += _rrf(kw_rank[rid], rrf_k)
            return s
    else:

        def score(row: dict) -> float:
            return float(row["sim"]) + weight * keyword_signal.get(row["record_id"], 0.0)

    out = []
    for row in kept_rows:
        annotated = dict(row)
        annotated["fusion"] = score(row)
        annotated["keyword_signal"] = keyword_signal.get(row["record_id"], 0.0)
        out.append(annotated)
    out.sort(key=lambda r: (-r["fusion"], vec_rank[r["record_id"]]))

    if {r["record_id"] for r in out} != set(vec_rank):
        raise FusionError(
            "재정렬이 후보 집합을 바꿨다 — 순서만 바꿔야 한다(P49 §4). 구현 오류."
        )
    return out


# ── 5. 컷 — 방식마다 다르다 (P48 §2.2) ───────────────────────────────────────

def apply_cut(
    rows: list[dict],
    *,
    method: str,
    tau: float,
    ratio: float,
    rrf_cutoff: float = 0.0,
) -> list[dict]:
    """fusion 결과에 컷을 건다. **방식마다 다르다**(P48 §2.2).

    **가중합** — 새 점수 분포에 맞춘 `tau` · `ratio` 를 받아야 한다. 기존 코사인 값을 그대로
    쓰면 안 된다. 재측정은 sweep 의 몫이고 이 함수는 받은 값을 걸 뿐이다.

    **RRF** — 코사인 `tau` 를 **fusion 점수에** 적용하지 않는다(스케일이 무관해 전부
    잘린다). 대신 두 관문을 둔다. **어느 것도 자동 통과가 아니다.**

        ① cosine eligibility gate   원래 코사인에 tau · ratio 를 건다.
                                    모든 후보가 코사인을 갖는다는 전제 위에 선다(fuse 참조)
        ② rrf_cutoff                fusion 점수 자체의 하한. 0 이면 끄지만 **끄는 것이
                                    기본값이라는 뜻이지 「없다」는 뜻이 아니다** — 값은
                                    실측으로 정한다

    ①과 ②는 서로를 대체하지 않는다. ①은 「이 Record 가 질의와 무관하다」를, ②는 「어느
    신호에서도 위로 오지 못했다」를 막는다.
    """
    if not rows:
        return rows

    if method == RRF:
        if tau <= 0 and ratio <= 0 and rrf_cutoff <= 0:
            return rows
        # 코사인이 없는 행은 존재할 수 없다(fuse 가 막는다). 그래서 자동 통과 경로가 없다.
        top = max(r["sim"] for r in rows)
        return [
            r for r in rows
            if r["sim"] >= tau and r["sim"] >= ratio * top
            and r["fusion"] >= rrf_cutoff
        ]

    if tau <= 0 and ratio <= 0:
        return rows
    top = rows[0]["fusion"]
    return [r for r in rows if r["fusion"] >= tau and r["fusion"] >= ratio * top]


# ── 6. IDF (참고용) ──────────────────────────────────────────────────────────

def idf_weights(
    contexts: list[ContextKeywords],
    presets: dict[int, Preset] | None = None,
) -> dict[int, float]:
    """keyword 별 IDF. **참고 실험 전용**(P48 §1-e).

    **문서 단위는 Record 다 — Context 가 아니다.** 검색이 Record 를 반환하고 신호도 Record
    단위로 집계하므로(§1-a) df 도 같은 단위여야 한다. Context 로 세면 Context 가 많은
    Record 가 df 를 부풀려 **흔한 keyword 를 희소한 것으로 보이게** 만든다.

    모집단은 신호로 실제 쓰이는 것과 같아야 한다.

        keyword_status = COMPLETED 인 Context 만    (§1-b — 그 외는 신호가 없다)
        BLOCKED 가 아닌 Preset 만                    (§1-c — 후보가 될 수 없다)

    Record 42건 규모에서 df 가 한 자릿수라 통계가 아니라 잡음이 된다. binary ·
    confidence 방식보다 우선하지 않으며, 결과 보고서에 일반화 한계를 명시해야 한다.
    """
    usable = [c for c in contexts if c.signal_usable]
    records = {c.record_id for c in usable}
    n = len(records)
    if n == 0:
        return {}

    # (keyword_id, record_id) 중복을 접어 **Record 단위**로 센다.
    seen: dict[int, set[int]] = {}
    for c in usable:
        for kid, _ in c.keywords:
            if presets is not None and (kid not in presets or not presets[kid].usable):
                continue
            seen.setdefault(kid, set()).add(c.record_id)
    return {kid: math.log(n / len(rs)) if rs else 0.0 for kid, rs in seen.items()}

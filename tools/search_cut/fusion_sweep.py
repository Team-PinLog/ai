"""Keyword fusion 오프라인 비교 — P48 1단계.

**DB 도 GMS 도 부르지 않는다.** `keyword_matrix.py` 가 만든 artifact 와 기존 벡터 행렬만
읽는다. 방식·가중치·top-k·하한을 바꿔 가며 훑는 것이 목적이므로, 한 번 뜬 artifact 위에서
모든 조합이 재구성돼야 한다(`sweep.py` 가 컷 격자에 대해 하는 것과 같은 성질).

    python tools/search_cut/fusion_sweep.py
    python tools/search_cut/fusion_sweep.py --keyword-matrix PATH --json OUT

지표는 `rank_score.py` 의 것을 **그대로 쓴다** — baseline 과 다른 자로 재면 비교가
성립하지 않으므로 정본을 하나로 둔다.

## 채택 조건 (P48 §6.1)

이 스크립트는 수치를 낼 뿐 채택하지 않는다. 판정은 사람이 하며 기준은 넷이다.

    단어형과 문장형을 합산한 평균만으로 채택하지 않는다
    기존 정답의 Hit@3 · Recall@3 퇴행이 없어야 한다
    최소 한 세그먼트에서 MRR 또는 nDCG 가 개선되어야 한다
    무관 질의 통과율이 유의미하게 나빠지면 기각한다

**Preset 으로 표현할 수 없는 질의를 별도 세그먼트로 낸다** — 이 신호가 닿지 않는 영역을
평균이 가리면 안 된다. `--floor` 위의 Preset 후보가 0건인 질의가 그것이다.

## 컷은 방식마다 다르다 (P48 §2.2)

`fusion.apply_cut` 이 갈라 처리한다. RRF 에 코사인 `τ_abs` 를 적용하지 않으며, 가중합은
**새 점수 분포에 맞춘 값을 받아야 한다** — 이 스크립트는 받은 값을 걸 뿐이고, 재측정은
`--tau` 격자를 훑어 사람이 정한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fusion as F  # noqa: E402
from rank_score import (  # noqa: E402
    GuardError, RATIO, SERVICE_LIMIT, TAU_ABS, TAU_ABS_WORD,
    _sha256, aggregate, is_word_query, metrics_for, zero_rate,
)

ROOT = Path(__file__).resolve().parents[2]
SEARCH = ROOT / ".search"

# cp949 콘솔에서 `—` 한 글자에 죽지 않게 한다(T28·T77). `keyword_matrix.py` 와 같은 방어다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

CASES = ("신한", "부캠", "그네", "스팟")


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ── 적재와 가드 ──────────────────────────────────────────────────────────────

def load(keyword_path: Path, search_dir: Path = SEARCH) -> tuple[dict, dict]:
    """행렬 셋과 keyword artifact 를 읽고 가드를 건다.

    `search_dir` 이 인자인 것은 **픽스처로 배관을 검증하기 위해서다** — 실측 전용인
    `.search/` 에 가짜 행렬을 넣지 않는다(`word_matrix.py` 의 포트 가드와 같은 원칙).
    """
    paths = {
        "matrix": search_dir / "matrix.json",
        "word_grid": search_dir / "word_grid.json",
        "recall_probe": search_dir / "recall_probe.json",
    }
    for k, p in paths.items():
        if not p.exists():
            raise GuardError(f"행렬이 없다: {p.relative_to(ROOT)}")
    if not keyword_path.exists():
        raise GuardError(
            f"keyword artifact 가 없다: {keyword_path}\n"
            "  python tools/search_cut/keyword_matrix.py  를 먼저 돌린다 "
            "(GMS 배치 1회 + DB 읽기)."
        )

    data = {k: json.loads(p.read_text(encoding="utf-8")) for k, p in paths.items()}
    kw = json.loads(keyword_path.read_text(encoding="utf-8"))

    # ① Profile 정합. 하나라도 어긋나면 비교가 성립하지 않는다.
    profiles = {k: d.get("profile") for k, d in data.items()}
    profiles["keyword_matrix"] = kw.get("profile")
    if len(set(profiles.values())) != 1 or None in profiles.values():
        raise GuardError(f"Profile 이 어긋난다: {profiles}")

    # ② 행렬에 context_id 가 있어야 keyword 를 조인할 수 있다.
    missing = [
        k for k, d in data.items()
        for sec in ("queries",)
        for e in (d.get(sec) or [])[:1]
        for r in (e.get("results") or [])[:1]
        if "context_id" not in r
    ]
    if missing:
        raise GuardError(
            f"행렬에 context_id 가 없다: {sorted(set(missing))}\n"
            "  word_matrix.py · recall_probe.py 를 다시 떠야 한다(P48 §4.1)."
        )

    # ③ 낡은 artifact 감지. Record 수와 Preset 판이 어긋나면 계산하지 않는다.
    src = (kw.get("source") or {}).get("matrices") or {}
    for name, m in src.items():
        cur = data[name.replace(".json", "")].get("record_count")
        if m.get("record_count") is not None and cur is not None and m["record_count"] != cur:
            raise GuardError(
                f"낡은 keyword artifact — {name} record_count "
                f"{m['record_count']} → {cur}. keyword_matrix.py 를 다시 돌린다."
            )
    return data, kw


def build_index(kw: dict) -> tuple[dict, dict, dict]:
    presets = {
        p["id"]: F.Preset(id=p["id"], version=p["version"], visibility=p["visibility"])
        for p in kw["presets"]
    }
    contexts = [
        F.ContextKeywords(
            context_id=c["context_id"],
            record_id=c["record_id"],
            keyword_status=c["keyword_status"],
            keywords=tuple((k["keyword_id"], k["confidence"]) for k in c["keywords"]),
        )
        for c in kw["contexts"]
    ]
    by_user: dict[int, list] = {}
    for c, raw in zip(contexts, kw["contexts"]):
        by_user.setdefault(raw["user_id"], []).append(c)

    query_cos = {
        e["query"]: {c["preset_id"]: c["cos"] for c in e["cos"]}
        for e in kw["query_preset"]
    }
    return presets, by_user, query_cos


# ── 한 조합 평가 ─────────────────────────────────────────────────────────────

def evaluate(
    entries: list[dict],
    *,
    answerable: bool,
    presets, by_user, query_cos, idf,
    method: str, weight: float, top_k: int, floor: float,
    null_policy: str, null_fill: float,
    tau: float, tau_word: float, ratio: float, limit: int,
    rrf_cutoff: float, rrf_k: float,
) -> tuple[dict, int]:
    """세그먼트 하나를 재고 (지표, Preset 미표현 질의 수)를 돌려준다."""
    per, unexpressible = [], 0
    for e in entries:
        q, rows = e["query"], (e.get("results") or [])
        n_rel = sum(1 for r in rows if r.get("is_expected"))

        qc = query_cos.get(q)
        if qc is None:
            raise GuardError(f"keyword artifact 에 질의가 없다: `{q}` — 다시 뜬다")
        cand = F.preset_candidates(qc, presets, top_k=top_k, floor=floor)
        if not cand:
            unexpressible += 1

        # **소유자가 없으면 신호가 조용히 0 이 된다.** `recall_probe.json` 은 `user_id` 를
        # 최상위에만 두므로 호출자가 행에 넣어 줘야 하고, 빠뜨리면 「keyword 가 아무것도
        # 못 올렸다」가 결론으로 나온다. 픽스처가 실제로 이 결함을 잡았다.
        uid = e.get("user_id")
        if uid is None:
            raise GuardError(f"질의 `{q}` 에 user_id 가 없다 — 신호를 조인할 수 없다")

        sig = F.record_signals(
            by_user.get(uid, []), cand, presets,
            method=method, null_policy=null_policy, null_fill=null_fill,
            idf=idf if method == F.IDF else None,
        )
        fused = F.fuse(rows, sig, method=method, weight=weight, limit=limit, rrf_k=rrf_k)
        kept = F.apply_cut(
            fused, method=method,
            tau=(tau_word if is_word_query(q) else tau), ratio=ratio,
            rrf_cutoff=rrf_cutoff,
        )
        per.append(metrics_for(kept, n_rel))

    agg = aggregate(per)
    if agg:
        agg["zero_rate"] = zero_rate(per)
    return agg, unexpressible


def case_ranks(entries, *, presets, by_user, query_cos, idf, **kw) -> dict:
    out = {}
    for e in entries:
        q = e["query"]
        if q not in CASES:
            continue
        rows = e.get("results") or []
        qc = query_cos.get(q, {})
        cand = F.preset_candidates(qc, presets, top_k=kw["top_k"], floor=kw["floor"])
        uid = e.get("user_id")
        if uid is None:
            raise GuardError(f"사례 `{q}` 에 user_id 가 없다 — 신호를 조인할 수 없다")
        sig = F.record_signals(
            by_user.get(uid, []), cand, presets,
            method=kw["method"], null_policy=kw["null_policy"],
            null_fill=kw["null_fill"], idf=idf if kw["method"] == F.IDF else None,
        )
        fused = F.fuse(rows, sig, method=kw["method"], weight=kw["weight"],
                       limit=kw["limit"], rrf_k=kw["rrf_k"])
        kept = F.apply_cut(
            fused, method=kw["method"],
            tau=(kw["tau_word"] if is_word_query(q) else kw["tau"]), ratio=kw["ratio"],
            rrf_cutoff=kw["rrf_cutoff"],
        )
        hit = next((i for i, r in enumerate(kept, 1) if r.get("is_expected")), None)
        out[q] = {"rank": hit, "returned": len(kept), "candidates": len(cand)}
    return out


# ── 실행 ─────────────────────────────────────────────────────────────────────

# (label, method, weight, rrf_cutoff) — 값은 출발점이며 채택값이 아니다.
BASE_GRID = [
    ("binary w=0.05", F.BINARY, 0.05),
    ("binary w=0.10", F.BINARY, 0.10),
    ("conf   w=0.10", F.CONFIDENCE, 0.10),
    ("conf   w=0.25", F.CONFIDENCE, 0.25),
    ("idf    w=0.10", F.IDF, 0.10),      # 참고용 — 주 채택 근거로 쓰지 않는다
]


def build_grid(cutoffs: list[float]) -> list[tuple]:
    """RRF 는 cutoff 후보만큼 행이 늘어난다 — `cutoff=0` 만으로는 채택할 수 없기 때문이다."""
    rows = [(lb, m, w, 0.0) for lb, m, w in BASE_GRID]
    for c in cutoffs:
        rows.append((f"rrf    c={c:g}", F.RRF, 0.0, c))
    return rows


def parse_cutoffs(grid: str, single: float) -> list[float]:
    if not grid.strip():
        return [single]
    out = []
    for x in grid.split(","):
        x = x.strip()
        if x:
            out.append(float(x))
    return out or [single]

COLS = ("n", "hit@1", "hit@3", "mrr", "recall@3", "ndcg@3", "zero_rate", "returned")


def row_str(label: str, m: dict, extra: str = "") -> str:
    if not m:
        return f"  {label:<16}  (0건)"
    cells = "".join(
        (str(m[c]).rjust(9) if c == "n" else f"{m[c]:.4f}".rjust(9)) for c in COLS
    )
    return f"  {label:<16}{cells}  {extra}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Keyword fusion 오프라인 비교 (P48 1단계)")
    ap.add_argument("--keyword-matrix", default=str(SEARCH / "keyword_matrix.json"))
    ap.add_argument("--search-dir", default=str(SEARCH),
                    help="행렬 디렉터리. 기본은 .search/ (실측 전용) — 픽스처 검증용 인자다")
    ap.add_argument("--json")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--floor", type=float, default=0.25)
    ap.add_argument("--null-policy", default=F.NULL_INCLUDE, choices=F.NULL_POLICIES)
    ap.add_argument("--null-fill", type=float, default=0.5)
    ap.add_argument("--tau", type=float, default=TAU_ABS)
    ap.add_argument("--tau-word", type=float, default=TAU_ABS_WORD)
    ap.add_argument("--ratio", type=float, default=RATIO)
    ap.add_argument("--limit", type=int, default=SERVICE_LIMIT)
    # RRF 의 fusion 점수 하한. **0 은 「개념이 없다」가 아니라 「실험 기본 실행에서 비활성」**
    # 이다. 채택값은 실측으로 정하며, `cutoff=0` 결과만으로 RRF 를 채택하지 않는다.
    ap.add_argument("--rrf-cutoff", type=float, default=0.0)
    # 양수 후보를 한 번에 훑는 축. `--rrf-cutoff` 는 이 격자가 없을 때의 단일값이다.
    ap.add_argument("--rrf-cutoff-grid", default="",
                    help='쉼표 구분 양수 후보. 예: "0,0.008,0.016"')
    # **rrf_k 가 바뀌면 RRF 점수 스케일이 바뀌므로 cutoff 를 다시 재야 한다.** 그래서 축으로
    # 노출하고 결과에 반드시 기록한다 — 하드코딩해 두면 바뀌어도 흔적이 남지 않는다.
    ap.add_argument("--rrf-k", type=float, default=60.0)
    args = ap.parse_args()

    kw_path = Path(args.keyword_matrix)
    search_dir = Path(args.search_dir)
    try:
        data, kw = load(kw_path, search_dir)
        presets, by_user, query_cos = build_index(kw)
    except GuardError as exc:
        print(f"[가드] {exc}", file=sys.stderr)
        return 1

    cutoffs = parse_cutoffs(args.rrf_cutoff_grid, args.rrf_cutoff)
    grid = build_grid(cutoffs)

    all_ctx = [c for cs in by_user.values() for c in cs]
    idf = F.idf_weights(all_ctx, presets)   # Record 기준 · 상태/visibility 반영(P48 §1-e)

    segs = {
        "문장형(정답)": (data["matrix"]["queries"], True),
        "단어형(정답)": (data["word_grid"]["queries"], True),
        "무관-문장형": (data["matrix"]["offtopic"], False),
        "무관-단어형": (data["word_grid"]["offtopic"], False),
    }

    log("=" * 100)
    log("Keyword fusion 비교 — P48 1단계")
    log("=" * 100)
    log(f"  Profile         {kw['profile']}")
    log(f"  Preset          {kw['preset_count']}건 · version={kw['preset_version']}")
    log(f"  query→Preset    top_k={args.top_k} · floor={args.floor}")
    log(f"  NULL 정책       {args.null_policy}"
        + (f" (fill={args.null_fill})" if args.null_policy == F.NULL_FILL else ""))
    log(f"  컷              tau={args.tau} · tau_word={args.tau_word} · r={args.ratio}")
    log(f"  RRF             k={args.rrf_k} · cutoff={args.rrf_cutoff}"
        + (f" · 격자={args.rrf_cutoff_grid}" if args.rrf_cutoff_grid else ""))
    log("  호출            DB 0회 · GMS 0회")

    common = dict(
        presets=presets, by_user=by_user, query_cos=query_cos, idf=idf,
        top_k=args.top_k, floor=args.floor,
        null_policy=args.null_policy, null_fill=args.null_fill,
        tau=args.tau, tau_word=args.tau_word, ratio=args.ratio, limit=args.limit,
        rrf_cutoff=args.rrf_cutoff, rrf_k=args.rrf_k,
    )
    result: dict = {
        "stage": "P48-1",
        "params": {k: v for k, v in vars(args).items() if k != "json"},
        "inputs": {
            **{k: _sha256(search_dir / f"{k}.json") for k in ("matrix", "word_grid")},
            "keyword_matrix": _sha256(kw_path),
        },
        "grid": {},
    }

    for seg_name, (entries, answerable) in segs.items():
        log(f"\n{seg_name}")
        log("  " + "방식".ljust(16) + "".join(c.rjust(9) for c in COLS)
            + "  Preset 미표현")
        for label, method, weight, cutoff in grid:
            try:
                m, unexpr = evaluate(
                    entries, answerable=answerable, method=method, weight=weight,
                    **{**common, "rrf_cutoff": cutoff},
                )
            except GuardError as exc:
                print(f"[가드] {exc}", file=sys.stderr)
                return 1
            note = f"{unexpr}건" if answerable else ""
            if method == F.RRF and cutoff <= 0:
                note = (note + "  ⚠ 잠정(cutoff=0 단독 채택 불가)").strip()
            log(row_str(label, m, note))
            result["grid"].setdefault(seg_name, {})[label] = {
                "metrics": m, "unexpressible": unexpr,
                "method": method, "weight": weight,
                # **실제 사용한 값을 행마다 기록한다.** 표만 보고 나중에 되짚을 수 없으면
                # 「어느 조건에서 나온 수치인가」가 사라진다.
                "rrf_cutoff": cutoff if method == F.RRF else None,
                "rrf_k": args.rrf_k if method == F.RRF else None,
                "adoptable_alone": not (method == F.RRF and cutoff <= 0),
            }

    log("\n사례별 (컷 후 정답 순위 · `—` 는 잘림)")
    log("  " + "방식".ljust(16) + "".join(q.rjust(10) for q in CASES))
    # `recall_probe.json` 은 소유자 한 명을 최상위에 두고 질의 행에는 넣지 않는다.
    # 여기서 내려 주지 않으면 신호 조인이 조용히 실패한다(위 가드가 그때 멈춘다).
    probe_uid = data["recall_probe"].get("user_id")
    probe = data["recall_probe"]["queries"]
    entries = [
        dict(e,
             user_id=e.get("user_id", probe_uid),
             results=[dict(r, is_expected=(r.get("name") == e.get("expect")))
                      for r in (e.get("results") or [])])
        for e in probe
    ]
    for label, method, weight, cutoff in grid:
        ranks = case_ranks(entries, method=method, weight=weight,
                           **{**common, "rrf_cutoff": cutoff})
        cells = "".join(
            (str(ranks[q]["rank"]) if ranks.get(q, {}).get("rank") else "—").rjust(10)
            for q in CASES
        )
        log(f"  {label:<16}{cells}")
        result.setdefault("cases", {})[label] = ranks

    log("\n읽는 법 (P48 §6.1)")
    log("  · 단어형·문장형 합산 평균만으로 채택하지 않는다.")
    log("  · Hit@3·Recall@3 퇴행이 없어야 하고, 최소 한 세그먼트에서 MRR 또는 nDCG 가 올라야 한다.")
    log("  · 무관 세그먼트는 zero_rate 가 떨어지면(=침묵이 무너지면) 기각 사유다.")
    log("  · `idf` 는 참고용이다 — Record 수가 작아 df 가 불안정하다. 주 채택 근거로 쓰지 않는다.")
    log("  · 「Preset 미표현」이 큰 세그먼트는 이 신호가 닿지 않는 영역이다. 평균이 가리지 않게 본다.")
    log("  · **`cutoff=0` 결과만으로 RRF 를 채택하지 않는다**(⚠ 표시). 양수 후보를 실측해")
    log("    Hit@3·Recall@3 · nDCG/MRR · 무관 zero_rate 를 비교하고, 채택 시 측정된 양수 값")
    log("    또는 0 유지 중 하나를 근거와 함께 명시한다.")
    log(f"  · rrf_k={args.rrf_k} 를 바꾸면 점수 스케일이 바뀌므로 cutoff 를 다시 재야 한다.")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"\n저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

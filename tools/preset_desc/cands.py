"""조건별 **후보 집합**이 어떻게 달라졌는가. `S15P11A705-228`. GMS 도 DB 도 안 부른다.

판정 회차를 돌리기 전에 이것부터 본다. `description`·`examples` 는 임베딩에 들어가므로
개정은 **판정 이전에 후보 선정에서 먼저 나타난다**. 후보가 안 움직였으면 판정 층에서
움직일 여지도 그만큼 좁다.

## base 대 base2 가 이 파일의 기준선이다

둘은 글자 하나까지 같은 조건이고, 갈리는 것은 **임베딩 API 의 비결정성뿐**이다(T61).
그 폭보다 작은 변화는 개정의 효과라고 부를 수 없다 — `-219` 가 판정 비결정성을 바닥으로
깐 것과 같은 자리다.

    python tools/preset_desc/cands.py
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from variants import TARGETS  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


def head(title: str) -> None:
    log("\n" + "=" * 78)
    log(title)
    log("=" * 78)


def cand_sets(data: dict, k: int, tau: float) -> dict[int, set[str]]:
    return {
        c["context_id"]: {
            x["code"] for x in c["candidates"] if x["rank"] <= k and x["sim"] >= tau
        }
        for c in data["contexts"]
    }


def sims_of(data: dict, code: str) -> dict[int, float]:
    return {
        c["context_id"]: x["sim"]
        for c in data["contexts"]
        for x in c["candidates"]
        if x["code"] == code
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / ".preset_desc"))
    ap.add_argument("--base", default="base")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--tau", type=float, default=0.30)
    ap.add_argument("--out", default=str(ROOT / ".preset_desc" / "cands.json"))
    args = ap.parse_args()

    d = Path(args.dir)
    mats = {}
    for f in sorted(d.glob("matrix-*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        mats[rec["condition"]] = rec
    if args.base not in mats:
        raise SystemExit(
            f"{args.base} 행렬이 없다. matrix.py 를 먼저 돌려라. 있는 것: {sorted(mats)}"
        )

    order = [args.base] + [
        c for c in ("base2", "D", "E", "DE") if c in mats and c != args.base
    ]
    base_sets = cand_sets(mats[args.base], args.k, args.tau)
    n_ctx = len(base_sets)

    head(f"후보 집합 — τ={args.tau} k={args.k} · Context {n_ctx}건")
    log(
        f"  {'조건':<8} {'후보평균':>8} {'후보0':>6} "
        f"{'base와 다른 Context':>20} {'추가':>6} {'제거':>6}"
    )
    log("  " + "─" * 62)

    rows = []
    for cond in order:
        sets = cand_sets(mats[cond], args.k, args.tau)
        sizes = [len(v) for v in sets.values()]
        diff = sum(1 for cid in base_sets if sets.get(cid, set()) != base_sets[cid])
        added = sum(len(sets.get(cid, set()) - base_sets[cid]) for cid in base_sets)
        removed = sum(len(base_sets[cid] - sets.get(cid, set())) for cid in base_sets)
        rows.append(
            {
                "condition": cond,
                "mean_candidates": round(st.mean(sizes), 3),
                "zero_candidate_contexts": sum(1 for x in sizes if x == 0),
                "contexts_differing_from_base": diff,
                "candidates_added": added,
                "candidates_removed": removed,
            }
        )
        log(
            f"  {cond:<8} {st.mean(sizes):>8.2f} {sum(1 for x in sizes if x == 0):>6} "
            f"{diff:>20} {added:>6} {removed:>6}"
        )

    if "base2" in mats:
        b2 = next(r for r in rows if r["condition"] == "base2")
        log()
        log(
            f"  **바닥** — base2 는 base 와 글자 하나까지 같다. 그런데도 Context "
            f"{b2['contexts_differing_from_base']}건에서 후보가 다르다"
        )
        log("  (임베딩 API 가 결정적이지 않다 — T61). 이 폭보다 작은 변화는 개정의 효과가 아니다.")

    head("고친 5종이 후보로 얼마나 오르는가")
    log("  개정이 노린 것은 **의미 범위를 좁혀 후보로 덜 오르게** 하는 것이다.")
    log()
    log(f"  {'프리셋':<14} " + " ".join(f"{c:>7}" for c in order))
    log("  " + "─" * (14 + 8 * len(order)))
    per_code = {}
    for code in TARGETS:
        cells = []
        for cond in order:
            sets = cand_sets(mats[cond], args.k, args.tau)
            cells.append(sum(1 for v in sets.values() if code in v))
        per_code[code] = dict(zip(order, cells))
        log(f"  {code:<14} " + " ".join(f"{x:>7}" for x in cells))

    log()
    log("  대조 — 고치지 않은 22종의 후보 등장 합계")
    others = []
    all_codes = {x["code"] for c in mats[args.base]["contexts"] for x in c["candidates"]}
    rest = sorted(all_codes - set(TARGETS))
    for cond in order:
        sets = cand_sets(mats[cond], args.k, args.tau)
        others.append(sum(1 for v in sets.values() for code in v if code in rest))
    log(f"  {'그 외 ' + str(len(rest)) + '종':<14} " + " ".join(f"{x:>7}" for x in others))

    head("고친 5종의 유사도 이동")
    log(f"  {'프리셋':<14} {'조건':<7} {'평균sim':>9} {'최대sim':>9} {'Δ평균 vs base':>15}")
    log("  " + "─" * 58)
    sim_rows = []
    for code in TARGETS:
        base_sims = sims_of(mats[args.base], code)
        base_mean = st.mean(base_sims.values())
        for cond in order:
            s = sims_of(mats[cond], code)
            m = st.mean(s.values())
            sim_rows.append(
                {"code": code, "condition": cond, "mean_sim": round(m, 6),
                 "max_sim": round(max(s.values()), 6), "delta_mean": round(m - base_mean, 6)}
            )
            log(
                f"  {code if cond == order[0] else '':<14} {cond:<7} {m:>9.4f} "
                f"{max(s.values()):>9.4f} {m - base_mean:>+15.4f}"
            )
        log()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"k": args.k, "tau": args.tau, "base": args.base, "conditions": rows,
             "target_candidate_counts": per_code, "other_codes": rest,
             "other_candidate_counts": dict(zip(order, others)), "target_sims": sim_rows},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    log(f"  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

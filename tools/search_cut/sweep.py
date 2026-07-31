"""`matrix.json` 을 읽어 검색 결과 컷 격자를 훑는다. **DB 도 GMS 도 부르지 않는다.**

컷은 유사도 하위만 자르므로 `LIMIT` 과 순서를 바꿔도 결과가 같다. 그래서 질의별 Record
전량의 유사도를 한 번 떠 두면 임의의 `(limit, τ_abs, r)` 에 대한 결과를 재구성할 수 있다.

    반환(limit, τ_abs, r) = { x ∈ 상위 limit개 : x.sim ≥ τ_abs  ∧  x.sim ≥ r × top1.sim }

`tools/tau_grid/sweep.py` 의 재구성이 **근사**였던 것(후보가 줄면 LLM 판정이 뒤집힐 수
있다)과 달리 **이쪽은 근사가 아니다.** 검색 경로에 LLM 이 없고 임베딩은 결정적이라,
같은 벡터에 같은 컷을 걸면 서버가 내놓을 결과와 정확히 같다.

셋을 **한 표에** 낸다. 꼬리만 세면 컷을 과하게 잡고, 그러면 정답이 조용히 사라진다.

    miss     기대 정답이 컷 때문에 사라진 질의 수
    empty    전부 잘려 0건이 된 질의 수          ← 가장 위험하다
    tail     무관한 결과가 얼마나 줄었나 (비관·낙관 두 기준)

    python tools/search_cut/sweep.py
    python tools/search_cut/sweep.py --limit 10 --tau 0.30,0.34 --ratio 0.6,0.7
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

for _s in (sys.stdout, sys.stderr):
    # T28. 이 파일은 질의와 장소명을 찍는다 — 콘솔이 cp949 면 첫 표에서 죽는다.
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


def head(title: str) -> None:
    log("\n" + "=" * 86)
    log(title)
    log("=" * 86)


HIT, PLAUSIBLE, IRRELEVANT = "hit", "plausible", "irrelevant"


def attach_labels(data: dict, labels: dict) -> None:
    """라벨을 결과 행에 붙인다. **어긋나면 재지 않고 멈춘다.**

    재시딩하면 record_id 가 바뀌고 라벨이 엉뚱한 행에 붙는다. 그 상태로도 숫자는
    나오므로(그래서 위험하다) 대조를 먼저 한다 — `tools/tau_grid/score.py` 와 같은 이유다.
    """
    by_query = {q["query"]: q for q in data["queries"]}
    seen = set()
    for entry in labels["queries"]:
        q = by_query.get(entry["query"])
        if q is None:
            raise SystemExit(f"labels.yaml 의 질의 '{entry['query']}' 가 matrix 에 없다. 재지 않고 멈춘다.")
        seen.add(entry["query"])
        ids = {x["record_id"]: x for x in q["results"]}
        marked = entry.get("plausible") or []
        for rid in marked:
            if rid not in ids:
                raise SystemExit(
                    f"labels.yaml: 질의 '{entry['query']}' 의 결과에 record_id={rid} 가 없다. "
                    "재시딩으로 id 가 바뀐 것으로 보인다. 재지 않고 멈춘다."
                )
            if ids[rid]["is_expected"]:
                raise SystemExit(
                    f"labels.yaml: record_id={rid} 는 질의 '{entry['query']}' 의 기대 정답이다. "
                    "plausible 로 적을 수 없다. 재지 않고 멈춘다."
                )
        for x in q["results"]:
            x["label"] = (
                HIT if x["is_expected"] else PLAUSIBLE if x["record_id"] in marked else IRRELEVANT
            )
    missing = [q["query"] for q in data["queries"] if q["query"] not in seen]
    if missing:
        raise SystemExit(f"labels.yaml 에 질의 {len(missing)}건이 빠졌다: {missing}. 재지 않고 멈춘다.")


def quantiles(name: str, values) -> dict:
    a = np.asarray(values, dtype=float)
    if a.size == 0:
        log(f"  {name:<26} n=0")
        return {"n": 0}
    row = {
        "n": int(a.size),
        "min": float(a.min()),
        "p25": float(np.percentile(a, 25)),
        "p50": float(np.percentile(a, 50)),
        "p75": float(np.percentile(a, 75)),
        "max": float(a.max()),
    }
    log(
        f"  {name:<26} n={row['n']:<5} min={row['min']:.4f} p25={row['p25']:.4f} "
        f"p50={row['p50']:.4f} p75={row['p75']:.4f} max={row['max']:.4f}"
    )
    return row


def distribution(data: dict) -> dict:
    """무엇이 겹치는지를 먼저 본다. **격자는 이 분포에서 나온다** — 임의의 0.2/0.3 이
    아니라 실제 대역에 맞춘다."""
    head("분포 — 컷을 어디에 둘 수 있는가")

    hits, plaus, irrel, top1, ratios = [], [], [], [], []
    for q in data["queries"]:
        rs = q["results"]
        t1 = rs[0]["sim"]
        top1.append(t1)
        for x in rs:
            ratios.append(x["sim"] / t1 if t1 else 0.0)
            {HIT: hits, PLAUSIBLE: plaus, IRRELEVANT: irrel}[x["label"]].append(x["sim"])

    out = {
        "hit": quantiles("기대 정답", hits),
        "plausible": quantiles("plausible", plaus),
        "irrelevant": quantiles("irrelevant", irrel),
        "query_top1": quantiles("질의 top-1", top1),
    }

    h, i = np.asarray(hits), np.asarray(irrel)
    lo, hi = float(h.min()), float(i.max())
    log()
    log(f"  기대 정답의 최솟값     {lo:.4f}   ← τ_abs 는 이 아래여야 정답을 잃지 않는다")
    log(f"  irrelevant 의 최댓값   {hi:.4f}")
    if hi > lo:
        # 두 분포가 겹치면 **어떤 τ_abs 도** 잡음을 다 자르면서 정답을 다 살릴 수 없다.
        # 이 폭이 이 티켓의 결론을 지배하므로 격자보다 먼저 찍는다(-210 과 같은 구조).
        both = int(((i >= lo) & (i <= hi)).sum())
        log(f"  → 겹침 구간 [{lo:.4f}, {hi:.4f}] 안에 irrelevant 가 {both}/{i.size}건 들어 있다")
        log("    단일 τ_abs 로는 이 구간을 가를 수 없다.")

    # 정답의 상대 유사도(top1 대비). r 컷의 안전 상한이 여기서 나온다.
    hit_ratio = [
        x["sim"] / q["results"][0]["sim"]
        for q in data["queries"]
        for x in q["results"]
        if x["label"] == HIT
    ]
    log()
    quantiles("기대 정답 r(=sim/top1)", hit_ratio)
    log(f"  기대 정답 r 의 최솟값  {min(hit_ratio):.4f}   ← r 은 이 아래여야 정답을 잃지 않는다")
    out["hit_ratio_min"] = float(min(hit_ratio))
    out["overlap"] = {"hit_min": lo, "irrelevant_max": hi}
    return out


def evaluate(data: dict, limit: int, tau: float, ratio: float, base_limit: int | None = None) -> dict:
    """한 조합의 결과. 셋을 함께 센다.

    꼬리 제거율의 **분모는 `base_limit` 로 고정한다.** limit 자체가 컷의 일종이라
    분모를 limit 과 함께 움직이면 「limit 을 줄여도 꼬리 제거 0%」라는 거짓 표가 나온다.
    기준선은 「계약값 limit · 컷 없음」이다.
    """
    base_limit = base_limit if base_limit is not None else limit
    miss, empty, returned = 0, 0, 0
    tail = {PLAUSIBLE: 0, IRRELEVANT: 0}
    base_tail = {PLAUSIBLE: 0, IRRELEVANT: 0}
    miss_detail = []
    for q in data["queries"]:
        base = q["results"][:base_limit]
        top1 = q["results"][0]["sim"]
        kept = [x for x in q["results"][:limit] if x["sim"] >= tau and x["sim"] >= ratio * top1]
        returned += len(kept)
        if not kept:
            empty += 1
        for x in base:
            if x["label"] in base_tail:
                base_tail[x["label"]] += 1
        for x in kept:
            if x["label"] in tail:
                tail[x["label"]] += 1
        had = any(x["label"] == HIT for x in base)
        has = any(x["label"] == HIT for x in kept)
        if had and not has:
            miss += 1
            hit = next(x for x in base if x["label"] == HIT)
            miss_detail.append(
                {"query": q["query"], "name": hit["name"], "sim": hit["sim"],
                 "rank": hit["rank"], "r": round(hit["sim"] / top1, 4)}
            )
    strict_base, loose_base = base_tail[IRRELEVANT], base_tail[IRRELEVANT] + base_tail[PLAUSIBLE]
    strict_cut = strict_base - tail[IRRELEVANT]
    loose_cut = loose_base - tail[IRRELEVANT] - tail[PLAUSIBLE]
    return {
        "limit": limit,
        "tau_abs": tau,
        "ratio": ratio,
        "returned": returned,
        "miss": miss,
        "empty": empty,
        "irrelevant_kept": tail[IRRELEVANT],
        "plausible_kept": tail[PLAUSIBLE],
        "tail_strict_removed": strict_cut,
        "tail_strict_base": strict_base,
        "tail_strict_pct": round(100 * strict_cut / strict_base, 1) if strict_base else 0.0,
        "tail_loose_removed": loose_cut,
        "tail_loose_base": loose_base,
        "tail_loose_pct": round(100 * loose_cut / loose_base, 1) if loose_base else 0.0,
        "miss_detail": miss_detail,
    }


def table(title: str, rows: list[dict], n_query: int) -> None:
    head(title)
    log(f"  {'limit':>5} {'τ_abs':>6} {'r':>5} {'반환':>5} {'정답누락':>8} {'빈결과':>7} "
        f"{'꼬리제거(비관)':>16} {'꼬리제거(낙관)':>16}")
    for r in rows:
        log(
            f"  {r['limit']:>5} {r['tau_abs']:>6.3f} {r['ratio']:>5.2f} {r['returned']:>5} "
            f"{r['miss']:>4}/{n_query:<3} {r['empty']:>4}/{n_query:<2} "
            f"{r['tail_strict_removed']:>6}/{r['tail_strict_base']:<3} {r['tail_strict_pct']:>5.1f}% "
            f"{r['tail_loose_removed']:>6}/{r['tail_loose_base']:<3} {r['tail_loose_pct']:>5.1f}%"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=str(ROOT / ".search" / "matrix.json"))
    ap.add_argument("--labels", default=str(HERE / "labels.yaml"))
    ap.add_argument("--limit", default="20,10", help="쉼표 구분. 공용 계약 08 §6.1 기본은 20")
    ap.add_argument("--tau", default="", help="비우면 0.00 및 0.20~0.44")
    ap.add_argument("--ratio", default="", help="비우면 0.00 및 0.40~0.90")
    ap.add_argument("--out", default=str(ROOT / ".search" / "sweep.json"))
    args = ap.parse_args()

    path = Path(args.matrix)
    if not path.exists():
        raise SystemExit(f"{path} 가 없다. matrix.py 를 먼저 돌려라.")
    data = json.loads(path.read_text(encoding="utf-8"))
    attach_labels(data, yaml.safe_load(Path(args.labels).read_text(encoding="utf-8")))

    limits = [int(x) for x in args.limit.split(",") if x.strip()]
    taus = ([float(x) for x in args.tau.split(",") if x.strip()]
            or [0.0] + [round(0.20 + 0.02 * i, 3) for i in range(13)])
    ratios = ([float(x) for x in args.ratio.split(",") if x.strip()]
              or [0.0] + [round(0.40 + 0.05 * i, 3) for i in range(11)])

    n_query = data["query_count"]
    log(f"  matrix: profile={data['profile']} · Record {data['record_count']}건 · 질의 {n_query}건")

    dist = distribution(data)
    base_limit = limits[0]

    tau_rows = [evaluate(data, base_limit, t, 0.0) for t in taus]
    table(f"τ_abs 단독 (limit={base_limit})", tau_rows, n_query)

    ratio_rows = [evaluate(data, base_limit, 0.0, r) for r in ratios]
    table(f"r 단독 (limit={base_limit})", ratio_rows, n_query)

    limit_rows = [
        evaluate(data, ll, 0.0, 0.0, base_limit=base_limit)
        for ll in sorted({*limits, 3, 5, 10, 20})
    ]
    table(f"limit 단독 — 컷 없이 상위 N 개만 (분모는 limit={base_limit})", limit_rows, n_query)

    combo = [evaluate(data, base_limit, t, r) for t in taus for r in ratios]
    safe = [c for c in combo if c["miss"] == 0 and c["empty"] == 0]
    safe.sort(key=lambda c: (-c["tail_strict_removed"], c["tau_abs"], c["ratio"]))
    table(
        f"조합 격자 (limit={base_limit}) — 정답 누락 0 · 빈 결과 0 인 조합 상위 12",
        safe[:12],
        n_query,
    )
    log(f"\n  {len(safe)}/{len(combo)} 조합이 정답 누락 0 · 빈 결과 0 이다.")

    # 안전선을 벗어나는 첫 지점을 함께 찍는다. 「어디까지 올릴 수 있나」보다
    # 「어디서부터 깨지나」가 채택 판단에 필요하다.
    head("안전선이 깨지는 지점")
    for name, rows in (("τ_abs", tau_rows), ("r", ratio_rows)):
        broke = next((r for r in rows if r["miss"] or r["empty"]), None)
        if broke is None:
            log(f"  {name:<6} 이 격자 안에서는 깨지지 않는다")
            continue
        key = broke["tau_abs"] if name == "τ_abs" else broke["ratio"]
        log(f"  {name:<6} {key:.3f} 에서 정답 누락 {broke['miss']}건 · 빈 결과 {broke['empty']}건")
        for d in broke["miss_detail"]:
            log(f"         「{d['query']}」 → {d['name']} sim={d['sim']:.4f} "
                f"({d['rank']}위 · r={d['r']:.3f})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"profile": data["profile"], "queries": n_query, "distribution": dist,
             "tau_only": tau_rows, "ratio_only": ratio_rows, "limit_only": limit_rows,
             "combo": combo},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    log(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

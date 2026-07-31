"""`matrix.json` 을 읽어 τ 를 훑는다. **DB 도 GMS 도 부르지 않는다.**

τ 를 올리면 후보에서 빠지는 것은 유사도 하위 프리셋이고, 그중 **현행 판정이 고른 것**만
결과에서 사라진다. 고르지 않은 후보가 빠지는 것은 결과를 바꾸지 않는다. 그래서 판정을
한 번 해 둔 행렬만 있으면 임의의 τ 에 대한 결과를 재판정 없이 재구성할 수 있다.

    재구성    selected(τ) = { k ∈ selected(0.30) : sim(ctx,k) ≥ τ }

**이 재구성은 근사다.** 후보 목록이 줄면 LLM 프롬프트가 달라지므로 남은 후보에 대한
판정이 뒤집힐 수 있다 — 특히 후보가 줄어 상대적으로 매력이 오른 항목을 새로 고를 수 있다.
그 방향의 오차는 **누락을 과대평가**하는 쪽이라 보수적이지만, 크기를 모르면 근사를 근거로
쓸 수 없다. `verify.py` 가 고른 τ 에서 실제 재판정을 돌려 이 근사와 대조한다.

    python tools/tau_grid/sweep.py                     # 분포 + 격자
    python tools/tau_grid/sweep.py --grid 0.30,0.35    # 격자 지정
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]

for _s in (sys.stdout, sys.stderr):
    # T28. 이 파일은 프리셋 한글 이름을 찍는다 — 콘솔이 cp949 면 첫 표에서 죽는다.
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


def quantiles(name: str, values) -> dict:
    a = np.asarray(values, dtype=float)
    if a.size == 0:
        log(f"  {name:<24} n=0")
        return {"n": 0}
    row = {
        "n": int(a.size),
        "min": float(a.min()),
        "p05": float(np.percentile(a, 5)),
        "p25": float(np.percentile(a, 25)),
        "p50": float(np.percentile(a, 50)),
        "p75": float(np.percentile(a, 75)),
        "p95": float(np.percentile(a, 95)),
        "max": float(a.max()),
    }
    log(
        f"  {name:<24} n={row['n']:<5} min={row['min']:.4f} p05={row['p05']:.4f} "
        f"p25={row['p25']:.4f} p50={row['p50']:.4f} p75={row['p75']:.4f} "
        f"p95={row['p95']:.4f} max={row['max']:.4f}"
    )
    return row


def distribution(data: dict, k: int) -> dict:
    """무엇이 겹치는지를 먼저 본다. τ 격자는 이 분포에서 나온다."""
    head("분포 — τ 를 어디에 둘 수 있는가")

    selected, unselected_in_k, top1, everything = [], [], [], []
    for c in data["contexts"]:
        cands = c["candidates"]
        top1.append(cands[0]["sim"])
        for x in cands:
            everything.append(x["sim"])
            if x["selected"]:
                selected.append(x["sim"])
            elif x["rank"] <= k:
                unselected_in_k.append(x["sim"])

    out = {
        "selected": quantiles("선택된 쌍", selected),
        "unselected_in_k": quantiles(f"미선택(top-{k} 내)", unselected_in_k),
        "context_top1": quantiles("Context top-1", top1),
        "all_pairs": quantiles("전체 쌍", everything),
    }

    s, u = np.asarray(selected), np.asarray(unselected_in_k)
    overlap_lo, overlap_hi = float(s.min()), float(u.max())
    log()
    log(f"  선택된 쌍의 최솟값   {overlap_lo:.4f}")
    log(f"  미선택 후보의 최댓값 {overlap_hi:.4f}")
    if overlap_hi > overlap_lo:
        # 두 분포가 겹치면 τ 를 어디에 두든 정상 선택을 함께 자른다. 이 폭이 이 티켓의
        # 결론을 지배하므로 격자보다 먼저 찍는다.
        both = ((s >= overlap_lo) & (s <= overlap_hi)).sum()
        log(
            f"  → 겹침 구간 [{overlap_lo:.4f}, {overlap_hi:.4f}] 안에 "
            f"선택된 쌍 {both}/{s.size}건이 들어 있다"
        )
    out["overlap"] = {"lo": overlap_lo, "hi": overlap_hi}
    return out


def by_category(data: dict) -> dict:
    """범주·프리셋별 선택 유사도. 단일 τ 가 어디를 먼저 자르는지 본다."""
    head("범주별 · 프리셋별 — 단일 τ 가 무엇을 먼저 자르는가")

    cat: dict[str, list[float]] = defaultdict(list)
    per_preset: dict[tuple, list[float]] = defaultdict(list)
    for c in data["contexts"]:
        for x in c["candidates"]:
            if x["selected"]:
                cat[x["category"]].append(x["sim"])
                per_preset[(x["code"], x["display_name"], x["category"])].append(x["sim"])

    out_cat = {}
    for name in sorted(cat):
        out_cat[name] = quantiles(name, cat[name])

    log()
    log(f"  {'프리셋':<28} {'선택':>4}  {'min':>7} {'p50':>7} {'max':>7}")
    rows = []
    for (code, disp, category), sims in sorted(
        per_preset.items(), key=lambda kv: (-len(kv[1]), kv[0][0])
    ):
        a = np.asarray(sims)
        log(
            f"  {code + ' ' + disp:<28} {a.size:>4}  "
            f"{a.min():>7.4f} {np.median(a):>7.4f} {a.max():>7.4f}"
        )
        rows.append(
            {
                "code": code,
                "display_name": disp,
                "category": category,
                "n": int(a.size),
                "min": float(a.min()),
                "p50": float(np.median(a)),
                "max": float(a.max()),
            }
        )

    # 한 번도 선택되지 않은 프리셋은 τ 와 무관하게 0 이다 — τ 의 리스크로 세면 안 된다.
    chosen_codes = {r["code"] for r in rows}
    all_codes = {
        (x["code"], x["display_name"])
        for c in data["contexts"]
        for x in c["candidates"]
    }
    never = sorted(c for c, _ in all_codes if c not in chosen_codes)
    if never:
        log()
        log(f"  현행 판정에서 한 번도 선택되지 않은 프리셋 {len(never)}개:")
        log(f"    {', '.join(never)}")
        log("    → 이 항목들의 0건은 τ 가 만든 것이 아니다. τ 를 낮춰도 0 이다")

    return {"by_category": out_cat, "by_preset": rows, "never_selected": never}


def sweep(data: dict, k: int, grid: list[float]) -> dict:
    """τ 격자. **오분류 감소분과 정상 판정 누락분을 같은 표에 둔다.**

    한쪽만 세면 τ 를 과하게 올리게 된다. 여기서는 「사라지는 선택」이 오분류인지 정상인지를
    가르지 않고 **총량**만 센다 — 가르는 것은 `labels.yaml` 을 붙이는 `score.py` 의 일이다.
    """
    head("τ 격자 — 재판정 없이 재구성")
    log(f"  {'τ':>6} {'후보수':>8} {'후보0':>6} {'선택유지':>8} {'선택유실':>8} "
        f"{'LLM호출':>8} {'유실Context':>12}")

    total_selected = sum(
        1 for c in data["contexts"] for x in c["candidates"] if x["selected"]
    )
    n_ctx = len(data["contexts"])

    rows = []
    for tau in grid:
        cand_total = 0
        zero_ctx = 0
        kept = 0
        lost = 0
        ctx_lost_any = 0
        lost_detail: list[dict] = []
        for c in data["contexts"]:
            in_k = [x for x in c["candidates"] if x["rank"] <= k]
            cands = [x for x in in_k if x["sim"] >= tau]
            cand_total += len(cands)
            if not cands:
                zero_ctx += 1
            c_lost = 0
            for x in in_k:
                if not x["selected"]:
                    continue
                if x["sim"] >= tau:
                    kept += 1
                else:
                    lost += 1
                    c_lost += 1
                    lost_detail.append(
                        {
                            "context_id": c["context_id"],
                            "code": x["code"],
                            "sim": x["sim"],
                            "body": c["body"][:40],
                        }
                    )
            if c_lost:
                ctx_lost_any += 1
        rows.append(
            {
                "tau": tau,
                "candidates_total": cand_total,
                "candidates_mean": round(cand_total / n_ctx, 2),
                "zero_candidate_contexts": zero_ctx,
                "selected_kept": kept,
                "selected_lost": lost,
                "llm_calls": n_ctx - zero_ctx,
                "contexts_losing_any": ctx_lost_any,
                "lost_detail": lost_detail,
            }
        )
        log(
            f"  {tau:>6.3f} {cand_total:>8} {zero_ctx:>6} {kept:>8} {lost:>8} "
            f"{n_ctx - zero_ctx:>8} {ctx_lost_any:>12}"
        )

    log()
    log(f"  기준: Context {n_ctx}건 · K={k} · 현행 선택 {total_selected}행")
    log("  「선택유실」은 오분류와 정상 판정을 **아직 가르지 않은** 총량이다.")
    log("  가르려면 labels.yaml 이 필요하다 — score.py 를 보라.")
    return {"k": k, "contexts": n_ctx, "selected_total": total_selected, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=str(ROOT / ".tau" / "matrix.json"))
    ap.add_argument("--k", type=int, default=10, help="KEYWORD_CANDIDATE_TOP_K")
    ap.add_argument("--grid", default="", help="쉼표 구분. 비우면 0.30~0.45 를 0.01 간격")
    ap.add_argument("--out", default=str(ROOT / ".tau" / "sweep.json"))
    args = ap.parse_args()

    path = Path(args.matrix)
    if not path.exists():
        raise SystemExit(f"{path} 가 없다. matrix.py 를 먼저 돌려라.")
    data = json.loads(path.read_text(encoding="utf-8"))

    grid = (
        [float(x) for x in args.grid.split(",") if x.strip()]
        if args.grid
        else [round(0.30 + 0.01 * i, 3) for i in range(16)]
    )

    log(f"  matrix: profile={data['profile']} · Context {data['context_count']}건 · "
        f"프리셋 {data['preset_count']}개 · 현행 판정 {data['selected_rows']}행")

    result = {
        "profile": data["profile"],
        "k": args.k,
        "distribution": distribution(data, args.k),
        "categories": by_category(data),
        "sweep": sweep(data, args.k, grid),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

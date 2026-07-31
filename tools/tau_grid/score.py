"""`labels.yaml` 을 붙여 τ 별 **오분류 감소분과 정상 판정 누락분을 함께** 센다.

`sweep.py` 는 「사라지는 선택」의 총량만 낸다. 총량만 보면 τ 를 올릴수록 좋아 보인다 —
사라지는 것에는 오분류도 정상도 함께 들어 있기 때문이다. 여기서 라벨로 그 둘을 가른다.

    이득   unfit 이 사라진다      오분류 감소
    손실   fit 이 사라진다        정상 판정 누락
    경계   unclear 가 사라진다    어느 쪽으로도 셀 수 있다 → 낙관·비관 두 값을 함께 낸다

**한쪽만 보고 τ 를 정하지 않기 위한 파일이다.**

    python tools/tau_grid/score.py
    python tools/tau_grid/score.py --grid 0.30,0.34,0.35
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

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


VERDICTS = ("fit", "unfit", "unclear")


def load_labels(path: Path) -> dict[tuple[int, str], dict]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[tuple[int, str], dict] = {}
    for row in doc["labels"]:
        if row["verdict"] not in VERDICTS:
            raise SystemExit(f"알 수 없는 verdict: {row}")
        key = (int(row["context"]), row["code"])
        if key in out:
            raise SystemExit(f"라벨이 중복이다: {key}")
        out[key] = row
    return out


def check_coverage(data: dict, labels: dict) -> list[tuple[int, str]]:
    """라벨이 현행 판정 83행을 **빠짐없이** 덮는지 본다.

    빠진 행이 있으면 그 행은 어느 집계에도 들어가지 않아 **조용히 사라진다.** τ 를
    정하는 근거가 그만큼 줄어든 것을 모른 채 결론을 내게 되므로 여기서 멈춘다.
    """
    actual = {
        (c["context_id"], x["code"])
        for c in data["contexts"]
        for x in c["candidates"]
        if x["selected"]
    }
    missing = sorted(actual - set(labels))
    extra = sorted(set(labels) - actual)
    log(f"  현행 판정 {len(actual)}행 · 라벨 {len(labels)}행")
    if missing:
        log(f"  [FAIL] 라벨이 없는 판정 행 {len(missing)}건: {missing[:8]}")
    if extra:
        # 재시딩으로 context_id 가 바뀌었거나 판정이 흔들린 것이다. 둘 다 라벨을 다시
        # 맞춰야 하는 상황이므로 경고로 끝내지 않는다.
        log(f"  [FAIL] 판정에 없는 라벨 {len(extra)}건: {extra[:8]}")
    if missing or extra:
        raise SystemExit("라벨과 판정이 어긋났다. 재지 않고 멈춘다.")
    return sorted(actual)


def label_distribution(data: dict, labels: dict) -> None:
    head("라벨 분포 — 유사도가 적합성을 가르는가")
    by: dict[str, list[float]] = defaultdict(list)
    for c in data["contexts"]:
        for x in c["candidates"]:
            if x["selected"]:
                by[labels[(c["context_id"], x["code"])]["verdict"]].append(x["sim"])
    for v in VERDICTS:
        a = np.asarray(by[v])
        if a.size == 0:
            continue
        log(
            f"  {v:<9} n={a.size:<4} min={a.min():.4f} p25={np.percentile(a,25):.4f} "
            f"p50={np.median(a):.4f} p75={np.percentile(a,75):.4f} max={a.max():.4f}"
        )
    fit, unfit = np.asarray(by["fit"]), np.asarray(by["unfit"])
    log()
    log(f"  fit 의 최솟값   {fit.min():.4f}")
    log(f"  unfit 의 최댓값 {unfit.max():.4f}")
    if unfit.max() > fit.min():
        caught = int((fit <= unfit.max()).sum())
        log(
            f"  → 겹친다. unfit 최댓값 아래에 fit 이 {caught}/{fit.size}건 있다 — "
            "τ 하나로 둘을 가를 수 없다"
        )
    log()
    log("  τ 를 unfit 최댓값 위로 올리면 그 아래 fit 을 **전부** 함께 자른다.")
    log("  아래 격자는 그 교환비를 τ 마다 적은 것이다.")


def score(data: dict, labels: dict, k: int, grid: list[float]) -> dict:
    head("τ 격자 — 오분류 감소분과 정상 판정 누락분")
    log(
        f"  {'τ':>6} │ {'unfit제거':>9} {'fit유실':>8} {'unclear유실':>12} │ "
        f"{'남은unfit':>9} {'남은fit':>8} │ {'후보0':>6} {'LLM호출':>8}"
    )
    log("  " + "─" * 76)

    n_ctx = len(data["contexts"])
    totals = defaultdict(int)
    for c in data["contexts"]:
        for x in c["candidates"]:
            if x["selected"]:
                totals[labels[(c["context_id"], x["code"])]["verdict"]] += 1

    rows = []
    for tau in grid:
        removed = defaultdict(int)
        kept = defaultdict(int)
        zero_ctx = 0
        lost_fit: list[dict] = []
        for c in data["contexts"]:
            in_k = [x for x in c["candidates"] if x["rank"] <= k]
            if not [x for x in in_k if x["sim"] >= tau]:
                zero_ctx += 1
            for x in in_k:
                if not x["selected"]:
                    continue
                v = labels[(c["context_id"], x["code"])]["verdict"]
                if x["sim"] >= tau:
                    kept[v] += 1
                else:
                    removed[v] += 1
                    if v == "fit":
                        lost_fit.append(
                            {"context_id": c["context_id"], "code": x["code"], "sim": x["sim"]}
                        )
        rows.append(
            {
                "tau": tau,
                "removed": {v: removed.get(v, 0) for v in VERDICTS},
                "kept": {v: kept.get(v, 0) for v in VERDICTS},
                "zero_candidate_contexts": zero_ctx,
                "llm_calls": n_ctx - zero_ctx,
                "lost_fit": lost_fit,
            }
        )
        log(
            f"  {tau:>6.3f} │ {removed['unfit']:>9} {removed['fit']:>8} "
            f"{removed['unclear']:>12} │ {kept['unfit']:>9} {kept['fit']:>8} │ "
            f"{zero_ctx:>6} {n_ctx - zero_ctx:>8}"
        )

    log()
    log(
        f"  기준: fit {totals['fit']} · unfit {totals['unfit']} · "
        f"unclear {totals['unclear']} (합 {sum(totals.values())}) · Context {n_ctx}건"
    )

    # 교환비. unfit 하나를 지우는 데 fit 을 몇 개 버리는가 — 이 숫자가 채택 근거다.
    head("교환비 — unfit 1건을 지우는 데 버리는 fit")
    log(f"  {'τ':>6} {'unfit제거':>9} {'fit유실':>8} {'fit/unfit':>10}   낙관/비관 순이득")
    for r in rows:
        ru, rf, rc = r["removed"]["unfit"], r["removed"]["fit"], r["removed"]["unclear"]
        if ru == 0:
            ratio = "—" if rf == 0 else "∞"
        else:
            ratio = f"{rf / ru:.2f}"
        # 낙관 = unclear 를 전부 오분류로 셈 · 비관 = 전부 정상으로 셈
        log(
            f"  {r['tau']:>6.3f} {ru:>9} {rf:>8} {ratio:>10}   "
            f"{ru + rc - rf:>+4} / {ru - rf - rc:>+4}"
        )
    log()
    log("  순이득 = 제거한 오분류 − 잃은 정상. **양수라야 τ 상향이 이득이다.**")
    log("  낙관은 unclear 를 전부 오분류로, 비관은 전부 정상으로 센 값이다.")

    per_ctx = by_context(data, labels, k, grid)
    return {"k": k, "totals": dict(totals), "rows": rows, "by_context": per_ctx}


def by_context(data: dict, labels: dict, k: int, grid: list[float]) -> list[dict]:
    """Context 단위. **행 단위 집계가 놓치는 것이 여기 있다.**

    티켓이 지목한 증상은 「무관한 Context 에 키워드가 붙는다」이지 「어떤 행이 틀렸다」가
    아니다. 행으로 세면 fit 하나를 잃는 것과 Context 하나가 통째로 비는 것이 같은 1 로
    잡히는데, 사용자에게는 전혀 다르다 — 앞은 키워드가 하나 줄고 뒤는 카드가 빈다.

    Context 를 τ 상향 뒤 상태로 셋으로 가른다.

        정당하게 비는 것   붙어 있던 것이 전부 unfit 이었다 → 비는 게 맞다
        부당하게 비는 것   fit 이 하나라도 있었는데 전부 사라졌다 → 카드가 빈다
        일부만 잃는 것     fit 이 남아 있다 → 손실이 눈에 덜 띈다
    """
    head("Context 단위 — 무엇이 비고, 비는 게 옳은가")
    log(
        f"  {'τ':>6} │ {'정당하게빔':>10} {'부당하게빔':>10} {'일부만잃음':>10} │ "
        f"{'무손실':>7}"
    )
    log("  " + "─" * 56)

    out = []
    for tau in grid:
        right_empty, wrong_empty, partial, intact = 0, 0, 0, 0
        wrong_ids = []
        for c in data["contexts"]:
            sel = [x for x in c["candidates"] if x["rank"] <= k and x["selected"]]
            if not sel:
                intact += 1  # 원래 0건이었다 — τ 가 바꿀 것이 없다
                continue
            before = [labels[(c["context_id"], x["code"])]["verdict"] for x in sel]
            survive = [
                labels[(c["context_id"], x["code"])]["verdict"]
                for x in sel
                if x["sim"] >= tau
            ]
            if survive:
                partial += 1 if len(survive) < len(sel) else 0
                intact += 1 if len(survive) == len(sel) else 0
            elif "fit" in before:
                wrong_empty += 1
                wrong_ids.append(c["context_id"])
            else:
                right_empty += 1
        out.append(
            {
                "tau": tau,
                "rightly_emptied": right_empty,
                "wrongly_emptied": wrong_empty,
                "partial_loss": partial,
                "intact": intact,
                "wrongly_emptied_ids": wrong_ids,
            }
        )
        log(
            f"  {tau:>6.3f} │ {right_empty:>10} {wrong_empty:>10} {partial:>10} │ "
            f"{intact:>7}"
        )

    log()
    log("  「정당하게 빔」이 이 티켓이 노리는 것이다 — 무관한 Context 에서 키워드가 사라진다.")
    log("  「부당하게 빔」은 그 대가다 — 맞는 키워드가 있었는데 카드가 빈다.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=str(ROOT / ".tau" / "matrix.json"))
    ap.add_argument("--labels", default=str(HERE / "labels.yaml"))
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--grid", default="")
    ap.add_argument("--out", default=str(ROOT / ".tau" / "score.json"))
    args = ap.parse_args()

    data = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    labels = load_labels(Path(args.labels))
    check_coverage(data, labels)

    grid = (
        [float(x) for x in args.grid.split(",") if x.strip()]
        if args.grid
        else [round(0.30 + 0.01 * i, 3) for i in range(16)]
    )

    label_distribution(data, labels)
    result = score(data, labels, args.k, grid)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

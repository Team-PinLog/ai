"""고친 5종과 **고치지 않은 22종**을 갈라 센다. `S15P11A705-228`. 파일만 읽는다.

## 왜 이 파일이 필요한가 — 자기충족 진단

계약이 지목한 위험이다. 라벨 42건을 보고 `description` 을 고치면 **그 42건에서만**
좋아지고, 그렇게 얻은 수치는 다음 데이터에서 재현되지 않는다(`-219` 가 프롬프트 예시로
같은 함정을 피했다).

이 티켓의 방어는 둘이다.

    수정 내용을 라벨과 무관하게 정했다   `variants.py` 머리말. 오답 본문이 아니라
                                          프리셋 텍스트 안의 교차 어휘만 보고 고쳤다
    고치지 않은 22종을 대조군으로 둔다   ← 이 파일

**42건에 과적합했다면 고친 5종에서만 좋아지고 22종은 제자리여야 한다.** 22종이 함께
나빠지면 개정이 후보 분포를 흔들어 부작용을 낸 것이고, 22종도 함께 좋아지면 그것은
과적합이 아니라 프리셋 텍스트의 성질을 건드린 것이다. 어느 쪽이든 행 수 총합만 보면
안 보인다.

    python tools/preset_desc/split_score.py --base base --cond DE
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools" / "prompt_ab"))
sys.path.insert(0, str(HERE))

from score_ab import load_labels, permutation_p, rows_of  # noqa: E402
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


def load_runs(d: Path) -> dict[str, list[dict]]:
    runs: dict[str, list[dict]] = {}
    for f in sorted(d.glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        runs.setdefault(rec["variant"], []).append(rec)
    for v in runs:
        runs[v].sort(key=lambda r: r["rep"])
    return runs


def split_tally(rec: dict, labels: dict) -> dict[str, dict[str, int]]:
    """한 회차를 **고친 5종 / 그 외** 로 갈라 라벨별로 센다."""
    out = {
        g: {"fit": 0, "unfit": 0, "unclear": 0, "unlabeled": 0}
        for g in ("targets", "others")
    }
    for key in rows_of(rec):
        g = "targets" if key[1] in TARGETS else "others"
        out[g][labels.get(key, "unlabeled")] += 1
    return out


def fmt(vals: list[int]) -> str:
    m = st.mean(vals)
    sd = st.stdev(vals) if len(vals) > 1 else 0.0
    return f"{m:6.2f} ± {sd:4.2f} [{min(vals)}~{max(vals)}]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="base")
    ap.add_argument("--cond", default="DE")
    ap.add_argument("--runs", default=str(ROOT / ".preset_desc" / "runs"))
    ap.add_argument("--labels", default=str(ROOT / "tools" / "tau_grid" / "labels.yaml"))
    ap.add_argument(
        "--extra", default=str(ROOT / "tools" / "prompt_ab" / "labels_extra.yaml")
    )
    ap.add_argument("--out", default=str(ROOT / ".preset_desc" / "split_score.json"))
    args = ap.parse_args()

    labels = load_labels(Path(args.labels), Path(args.extra))
    runs = load_runs(Path(args.runs))
    missing = {args.base, args.cond} - set(runs)
    if missing:
        raise SystemExit(f"회차가 없는 조건: {sorted(missing)}. 있는 것: {sorted(runs)}")

    A, B = runs[args.base], runs[args.cond]
    # **관측 수를 맞춘다.** 순열검정은 표본 크기에 민감하고, 조건마다 회차가 다르면
    # 「어느 조건이 더 많이 돌았나」가 p 에 섞인다(`-223` T59 와 같은 이유).
    n = min(len(A), len(B))
    A, B = A[:n], B[:n]
    log(f"  기준 {args.base} ↔ 비교 {args.cond} · 관측 {n}개씩")
    log(f"  고친 5종 {TARGETS}")

    result: dict[str, dict] = {}
    for group, title in (
        ("targets", "고친 5종 소관 행"),
        ("others", "고치지 않은 22종 소관 행"),
    ):
        head(f"{title} — {args.base} 대 {args.cond}")
        result[group] = {}
        for verdict, better in (
            ("unfit", "낮을수록"), ("fit", "높을수록"),
            ("unclear", "낮을수록"), ("unlabeled", "낮을수록"),
        ):
            a = [split_tally(r, labels)[group][verdict] for r in A]
            b = [split_tally(r, labels)[group][verdict] for r in B]
            delta = st.mean(b) - st.mean(a)
            disjoint = (max(a) < min(b)) or (max(b) < min(a))
            p = permutation_p(a, b)
            log(
                f"  {verdict:<10}({better}) {args.base} {fmt(a)}  "
                f"{args.cond} {fmt(b)}  Δ {delta:+6.2f}  "
                f"범위 {'분리' if disjoint else '겹침'}  p={p:.4f}"
            )
            result[group][verdict] = {
                "a": a, "b": b, "delta": delta, "disjoint": disjoint, "p": p
            }

    head("판독")
    t_unfit = result["targets"]["unfit"]["delta"]
    o_unfit = result["others"]["unfit"]["delta"]
    t_fit = result["targets"]["fit"]["delta"]
    o_fit = result["others"]["fit"]["delta"]
    log(f"  고친 5종      오분류 {t_unfit:+.2f}행 · 정상 {t_fit:+.2f}행")
    log(f"  안 고친 22종  오분류 {o_unfit:+.2f}행 · 정상 {o_fit:+.2f}행")
    log()
    # **부호만 보고 판정하지 않는다.** 이동이 -0.40 이고 p=0.31 인 것을 「대가를 치른다」로
    # 찍으면 회차 운을 부작용으로 읽는다. 이 파일이 겨눈 것은 과적합이지 노이즈가 아니다.
    o_fit_real = o_fit < 0 and (
        result["others"]["fit"]["disjoint"] or result["others"]["fit"]["p"] < 0.05
    )
    o_unfit_worse = o_unfit > 0 and (
        result["others"]["unfit"]["disjoint"] or result["others"]["unfit"]["p"] < 0.05
    )
    if t_unfit >= 0:
        log("  → 겨눈 5종에서조차 줄지 않았다. 개정이 표적에 닿지 않았다")
    elif o_fit_real or o_unfit_worse:
        log("  → 고친 것에서 줄었으나 **안 고친 22종이 대가를 치른다**(유의). 개정이")
        log("    후보 분포를 흔들어 다른 프리셋을 밀어낸 것이다 — 총합만 보면 안 보인다")
    else:
        log("  → 고친 것에서 줄고 **안 고친 22종의 이동은 유의하지 않다.**")
        log("    과적합의 전형적 모양(5종만 개선·22종 악화)이 아니다")
    log(
        f"    (안 고친 22종 정상 Δ {o_fit:+.2f} p={result['others']['fit']['p']:.4f} · "
        f"오분류 Δ {o_unfit:+.2f} p={result['others']['unfit']['p']:.4f})"
    )
    log()
    log("  **라벨 없는 행이 어느 무리에서 나오는지도 함께 본다** — 후보 슬롯이 k 개로")
    log("  고정이라 겨눈 프리셋을 좁히면 다른 프리셋이 그 자리를 가져간다.")
    log(
        f"    고친 5종 {result['targets']['unlabeled']['delta']:+.2f} · "
        f"안 고친 22종 {result['others']['unlabeled']['delta']:+.2f}"
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"base": args.base, "cond": args.cond, "n_obs": n,
             "targets": list(TARGETS), "groups": result},
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )
    log(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

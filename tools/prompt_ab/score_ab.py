"""회차 파일들을 라벨에 붙여 **개선 폭과 비결정성을 같은 자로 잰다.**

    python tools/prompt_ab/score_ab.py
    python tools/prompt_ab/score_ab.py --dump-unlabeled   # 라벨이 필요한 행만 낸다

파일만 읽는다. DB 도 GMS 도 부르지 않는다.

## 이 파일이 답해야 하는 질문

`-210` 이 실측한 것 하나가 이 티켓의 측정 전체를 규정한다 — **같은 프롬프트로 재판정만
해도 Context 11/42(26%)에서 결과가 달라진다.** 그러므로 「B 가 A 보다 오분류 2건 적다」는
문장은 그 자체로는 아무것도 말하지 않는다. 2건이 개선인지 흔들림인지 가르려면 **흔들림의
크기를 같은 실험 안에서 재서 나란히 놓아야** 한다.

그래서 A 를 5회, B 를 5회 돌린다. 그러면 두 가지를 함께 얻는다.

    비결정성   A 회차끼리의 차이 · B 회차끼리의 차이      ← 같은 조건인데 갈리는 폭
    조건 효과  A 평균과 B 평균의 차이                     ← 프롬프트가 만든 폭

**뒤가 앞보다 크지 않으면 개선이라고 부르지 않는다.**

## 라벨이 없는 행

`labels.yaml` 은 현행 판정 83행만 덮는다. 재판정하면 그 목록에 없는 조합이 나온다 —
비결정성이 새 선택을 만들기 때문이다(`-210` §4 의 `+9`). 그 행들을 빼고 세면 **B 가 새로
고른 것이 전부 없는 셈이 되어** 조건 비교가 A 쪽으로 기운다. 그래서 `labels_extra.yaml`
로 라벨을 넓히고, 그래도 남는 것은 `unlabeled` 로 따로 세어 **fit·unfit 양극단으로 돌려
결론이 뒤집히는지** 본다(`unclear` 를 낙관·비관으로 돌리는 것과 같은 취급).
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from itertools import combinations
from pathlib import Path

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


def load_labels(*paths: Path) -> dict[tuple[int, str], str]:
    """여러 라벨 파일을 합친다. **중복 키는 오류다** — 어느 쪽이 이겼는지 모르게 되면
    「어떤 기준으로 셌나」를 사후에 재현할 수 없다."""
    out: dict[tuple[int, str], str] = {}
    for p in paths:
        if not p.exists():
            continue
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for row in doc.get("labels", []):
            if row["verdict"] not in VERDICTS:
                raise SystemExit(f"알 수 없는 verdict: {row}")
            key = (int(row["context"]), row["code"])
            if key in out:
                raise SystemExit(f"라벨이 중복이다: {key} ({p.name})")
            out[key] = row["verdict"]
    return out


def load_runs(d: Path) -> dict[str, list[dict]]:
    runs: dict[str, list[dict]] = {}
    for f in sorted(d.glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        runs.setdefault(rec["variant"], []).append(rec)
    for v in runs:
        runs[v].sort(key=lambda r: r["rep"])
    return runs


def rows_of(rec: dict) -> set[tuple[int, str]]:
    return {(int(cid), code) for cid, codes in rec["selections"].items() for code in codes}


def tally(rec: dict, labels: dict) -> dict:
    """한 회차의 선택을 라벨별로 센다 — 행 단위와 Context 단위를 함께."""
    counts = {v: 0 for v in VERDICTS}
    counts["unlabeled"] = 0
    for key in rows_of(rec):
        counts[labels.get(key, "unlabeled")] += 1

    # Context 단위. 행으로만 세면 「키워드 하나가 줄었다」와 「카드가 통째로 빈다」가
    # 같은 1 로 잡힌다(`tau_grid/score.py:by_context` 와 같은 이유).
    empty = 0
    no_fit = 0
    for cid, codes in rec["selections"].items():
        if not codes:
            empty += 1
            continue
        if not any(labels.get((int(cid), c)) == "fit" for c in codes):
            no_fit += 1
    counts["empty_contexts"] = empty
    counts["contexts_without_fit"] = no_fit + empty
    return counts


def ctx_diff(a: dict, b: dict) -> int:
    """두 회차가 **몇 개 Context 에서 다른 답을 냈나.** `-210` 의 11/42 와 같은 자다."""
    keys = set(a["selections"]) | set(b["selections"])
    return sum(
        1 for k in keys if set(a["selections"].get(k, [])) != set(b["selections"].get(k, []))
    )


def spread(name: str, runs: list[dict]) -> list[int]:
    """같은 조건의 회차끼리 얼마나 갈리는가 — 이것이 비교의 바닥이다."""
    pairs = [ctx_diff(x, y) for x, y in combinations(runs, 2)]
    n = len(runs[0]["selections"])
    log(
        f"  {name} 회차끼리   쌍 {len(pairs)}개 · 어긋난 Context "
        f"min {min(pairs)} / 중앙 {int(st.median(pairs))} / max {max(pairs)}  (/{n})"
    )
    return pairs


def series(runs: list[dict], labels: dict, key: str) -> list[int]:
    return [tally(r, labels)[key] for r in runs]


def fmt(vals: list[int]) -> str:
    m = st.mean(vals)
    sd = st.stdev(vals) if len(vals) > 1 else 0.0
    return f"{m:6.2f} ± {sd:4.2f}  [{min(vals)}~{max(vals)}]"


def permutation_p(a: list[int], b: list[int]) -> float:
    """평균 차이에 대한 **양측 순열검정**. 전수라 근사가 아니다.

    회차 5~10개씩이면 t 검정의 정규성 가정을 받쳐 줄 것이 없다. 순열검정은 그 가정을
    쓰지 않는다 — 「조건 이름이 결과와 무관하다」가 참이라면 20개 관측을 두 무리로 나눈
    어떤 방식이든 똑같이 그럴듯하므로, 그 전부를 세어 관측된 차이만큼 극단적인 것이
    몇 분의 몇인지 보면 된다. n=10+10 이면 184,756 가지로 전수 계산이 끝난다.

    **「범위 비중첩」을 대체하지 않고 함께 낸다.** 비중첩은 사전에 정한 기준이고, 이것은
    표본을 늘린 뒤에 더 정밀하게 보려고 더한 자다. 나중에 더한 자가 통과했다고 앞의
    자를 무르면 그것은 기준을 결과에 맞춘 것이다 — 둘 다 적고 어느 쪽이 통과했는지 밝힌다.
    """
    obs = abs(st.mean(b) - st.mean(a))
    pool = a + b
    na = len(a)
    hits = total = 0
    for combo in combinations(range(len(pool)), na):
        left = [pool[i] for i in combo]
        right = [pool[i] for i in range(len(pool)) if i not in set(combo)]
        total += 1
        if abs(st.mean(right) - st.mean(left)) >= obs - 1e-12:
            hits += 1
    return hits / total


def report_metric(label: str, a: list[int], b: list[int], lower_is_better: bool) -> dict:
    """한 지표에 대해 A·B 를 나란히 놓고 **범위가 겹치는지**까지 판정한다.

    평균 차이만 보면 회차가 우연히 갈린 것을 효과로 읽는다. 회차 5개씩이면 모수 검정을
    걸 표본이 못 되므로, **두 조건의 관측 범위가 아예 겹치지 않는가**라는 비모수 조건을
    쓴다. 겹치지 않으면 10개 관측이 조건별로 완전히 갈렸다는 뜻이고, 효과가 없을 때
    그렇게 될 확률은 1/C(10,5) ≈ 0.4% 다.
    """
    delta = st.mean(b) - st.mean(a)
    good = delta < 0 if lower_is_better else delta > 0
    disjoint = (max(a) < min(b)) or (max(b) < min(a))
    p = permutation_p(a, b)
    mark = "분리" if disjoint else "겹침"
    log(f"  {label:<22} A {fmt(a)}   B {fmt(b)}   Δ {delta:+6.2f}  범위 {mark}  p={p:.4f}")
    return {
        "a": a, "b": b, "delta": delta, "disjoint": disjoint, "p": p,
        "improved": good and disjoint,
        "improved_p": good and p < 0.05,
    }


def stability(runs: dict[str, list[dict]], labels: dict, order: list[str]) -> list[dict]:
    """오분류가 **늘 붙는 것인가, 흔들릴 때 붙는 것인가.**

    행 수만 세면 이 둘이 섞인다. 그런데 처방이 정반대다 — 늘 붙는 것은 규칙으로 겨눌 수
    있지만, 흔들릴 때만 붙는 것은 겨눌 대상이 매번 다르다. `-210` §4 가 중복 본문 5쌍에서
    같은 것을 봤고(한쪽에만 나타난 `CELEBRATION`·`VIEW_GOOD` 이 전부 unfit), 여기서는
    회차 5개로 직접 센다.
    """
    head("오분류의 성격 — 늘 붙는가, 흔들릴 때 붙는가")
    freq: dict[tuple[int, str], dict[str, int]] = {}
    for v, rs in runs.items():
        for r in rs:
            for key in rows_of(r):
                freq.setdefault(key, {})[v] = freq.setdefault(key, {}).get(v, 0) + 1

    n = {v: len(rs) for v, rs in runs.items()}
    unfit_rows = sorted(
        (k for k in freq if labels.get(k) == "unfit"),
        key=lambda k: -sum(freq[k].get(v, 0) for v in order),
    )
    log(f"  {'행':<26} " + " ".join(f"{v:>3}" for v in order) + f"   (회차 {n})")
    log("  " + "─" * (26 + 4 * len(order) + 12))
    for k in unfit_rows:
        log(f"  {str(k[0]) + ' ' + k[1]:<26} " + " ".join(f"{freq[k].get(v, 0):>3}" for v in order))

    log()
    out = []
    for v in order:
        always = [k for k in unfit_rows if freq[k].get(v, 0) == n[v]]
        sometimes = [k for k in unfit_rows if 0 < freq[k].get(v, 0) < n[v]]
        out.append({"variant": v, "always": [list(k) for k in always],
                    "sometimes": [list(k) for k in sometimes]})
        log(
            f"  {v}  늘 붙는 오분류 {len(always)}종 · 흔들릴 때만 붙는 것 {len(sometimes)}종"
        )
    log()
    log(f"  현행 판정이 낸 오분류는 10종이었다(`labels.yaml`). 재판정에서 나타난 것은 "
        f"{len(unfit_rows)}종이다.")
    log("  **오분류의 레퍼토리가 회차마다 다르다.** 규칙이 겨눌 고정된 표적이 없다.")
    fit_shaky = [
        k for k in freq
        if labels.get(k) == "fit" and any(0 < freq[k].get(v, 0) < n[v] for v in order)
    ]
    log(f"  대조 — 흔들리는 fit 은 {len(fit_shaky)}종뿐이다. **정상 판정은 안정적이고 "
        "오분류만 흔들린다.**")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="A", help="기준 조건")
    ap.add_argument("--cond", default="B", help="비교할 조건")
    ap.add_argument("--runs", default=str(ROOT / ".prompt_ab" / "runs"))
    ap.add_argument("--labels", default=str(ROOT / "tools" / "tau_grid" / "labels.yaml"))
    ap.add_argument("--extra", default=str(HERE / "labels_extra.yaml"))
    ap.add_argument("--matrix", default=str(ROOT / ".tau" / "matrix.json"))
    ap.add_argument("--out", default=str(ROOT / ".prompt_ab" / "score.json"))
    ap.add_argument(
        "--dump-unlabeled",
        action="store_true",
        help="라벨이 없는 행을 본문·의미와 함께 낸다. **어느 조건이 골랐는지는 찍지 않는다** — "
        "그것을 보고 라벨을 붙이면 라벨이 결론을 따라간다",
    )
    args = ap.parse_args()

    labels = load_labels(Path(args.labels), Path(args.extra))
    runs = load_runs(Path(args.runs))
    if not runs:
        raise SystemExit(f"회차 파일이 없다: {args.runs}")

    if args.dump_unlabeled:
        return dump_unlabeled(runs, labels, Path(args.matrix))

    head("회차")
    for v, rs in sorted(runs.items()):
        fails = sum(len(r["failures"]) for r in rs)
        models = sorted({m for r in rs for m in r["models"]})
        log(
            f"  {v}  회차 {len(rs)}개 (r{rs[0]['rep']}~r{rs[-1]['rep']}) · "
            f"LLM {sum(r['llm_calls'] for r in rs)}회 · 실패 {fails} · 모델 {models}"
        )
        if fails:
            log(f"     [주의] 실패한 호출이 있다 — 그 Context 는 선택 0건으로 잡힌다")

    missing = {args.base, args.cond} - set(runs)
    if missing:
        raise SystemExit(f"회차가 없는 조건: {sorted(missing)}. 있는 것: {sorted(runs)}")
    A, B = runs[args.base], runs[args.cond]
    log()
    log(f"  기준 {args.base}  ↔  비교 {args.cond}   (아래 표의 A·B 는 이 둘이다)")

    head("바닥 — 같은 조건인데 얼마나 갈리는가 (비결정성)")
    log("  이 폭보다 작은 차이는 개선이라고 부를 수 없다. `-210` 은 이 값을 11/42(26%)로 쟀다.")
    log()
    pa, pb = spread(args.base, A), spread(args.cond, B)
    cross = [ctx_diff(x, y) for x in A for y in B]
    n_ctx = len(A[0]["selections"])
    log(
        f"  {args.base} 대 {args.cond}         쌍 {len(cross)}개 · 어긋난 Context "
        f"min {min(cross)} / 중앙 {int(st.median(cross))} / max {max(cross)}  (/{n_ctx})"
    )
    within = pa + pb
    log()
    log(
        f"  → 같은 조건 중앙 {int(st.median(within))} · 다른 조건 중앙 {int(st.median(cross))}"
    )
    if st.median(cross) <= st.median(within):
        log("    프롬프트를 바꿔도 **같은 프롬프트를 다시 돌린 것만큼밖에** 안 달라진다.")
    else:
        log("    조건이 만드는 차이가 흔들림보다 크다. 어느 쪽으로 큰지는 아래에서 본다.")

    head("지표 — 오분류·정상 판정을 함께")
    log("  회차 5개의 평균 ± 표준편차 [최소~최대]. **범위가 겹치면 회차 운으로 뒤집힌다.**")
    log()
    m = {}
    m["unfit"] = report_metric("오분류 unfit 행", series(A, labels, "unfit"),
                               series(B, labels, "unfit"), lower_is_better=True)
    m["fit"] = report_metric("정상 fit 행", series(A, labels, "fit"),
                             series(B, labels, "fit"), lower_is_better=False)
    m["unclear"] = report_metric("경계 unclear 행", series(A, labels, "unclear"),
                                 series(B, labels, "unclear"), lower_is_better=True)
    m["unlabeled"] = report_metric("라벨 없는 행", series(A, labels, "unlabeled"),
                                   series(B, labels, "unlabeled"), lower_is_better=True)
    log()
    m["empty"] = report_metric("선택 0건 Context", series(A, labels, "empty_contexts"),
                               series(B, labels, "empty_contexts"), lower_is_better=True)
    m["no_fit"] = report_metric("fit 0건 Context", series(A, labels, "contexts_without_fit"),
                                series(B, labels, "contexts_without_fit"), lower_is_better=True)
    log()
    log("  「fit 0건 Context」가 사용자가 보는 손실이다 — 카드에 맞는 키워드가 하나도 없다.")

    head("교환비 — unfit 1건을 지우는 데 버리는 fit")
    d_unfit = st.mean(m["unfit"]["a"]) - st.mean(m["unfit"]["b"])   # 줄인 오분류(+가 이득)
    d_fit = st.mean(m["fit"]["a"]) - st.mean(m["fit"]["b"])         # 잃은 정상(+가 손실)
    d_unclear = st.mean(m["unclear"]["a"]) - st.mean(m["unclear"]["b"])
    d_unlab = st.mean(m["unlabeled"]["a"]) - st.mean(m["unlabeled"]["b"])
    ratio = "—" if d_unfit == 0 else f"{d_fit / d_unfit:.2f}"
    log(f"  줄인 오분류 {d_unfit:+.2f} · 잃은 정상 {d_fit:+.2f} · 교환비 {ratio}")
    log(f"  `-210` 은 τ 에서 교환비 **1.22 를 최선으로도 기각**했다. 같은 자를 여기에도 댄다.")
    log()
    # unclear·unlabeled 를 양극단으로 돌린다. 결론이 그 처리에 걸려 있으면 결론이 아니다.
    log("  순이득 = 줄인 오분류 − 잃은 정상. unclear·라벨없음을 양극단으로 돌린 값이다.")
    best = d_unfit + d_unclear + d_unlab - d_fit   # 경계를 전부 오분류로 셈
    worst = d_unfit - d_unclear - d_unlab - d_fit  # 경계를 전부 정상으로 셈
    log(f"    낙관(경계=오분류) {best:+.2f}   ·   비관(경계=정상) {worst:+.2f}")
    if best > 0 and worst > 0:
        log("    → 두 극단 모두 양수. 경계 라벨의 처리가 결론을 바꾸지 않는다")
    elif best <= 0 and worst <= 0:
        log("    → 두 극단 모두 음수 이하. 경계 라벨의 처리와 무관하게 이득이 없다")
    else:
        log("    → **부호가 갈린다.** 경계 행의 처리가 결론을 뒤집는다 — 결론이라고 부를 수 없다")

    head("판정")
    ok = m["unfit"]["improved"]
    log(f"  오분류가 줄었는가                {'그렇다' if d_unfit > 0 else '아니다'} ({d_unfit:+.2f}행)")
    log(f"  [사전 기준] 범위가 분리되는가    {'그렇다' if ok else '아니다'} "
        f"({args.base} {min(m['unfit']['a'])}~{max(m['unfit']['a'])} · "
        f"{args.cond} {min(m['unfit']['b'])}~{max(m['unfit']['b'])})")
    log(f"  [보강 기준] 순열검정 p<0.05      "
        f"{'그렇다' if m['unfit']['improved_p'] else '아니다'} (p={m['unfit']['p']:.4f})")
    log(f"  정상 판정을 얼마나 잃는가        {d_fit:+.2f}행 · fit 0건 Context {m['no_fit']['delta']:+.2f}건")
    log(f"  순이득의 부호가 안정적인가       {'그렇다' if (best > 0) == (worst > 0) else '아니다'}")

    stab = stability(runs, labels, sorted(runs))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "base": args.base,
                "cond": args.cond,
                "stability": stab,
                "n_reps": {v: len(rs) for v, rs in runs.items()},
                "within_condition_ctx_diff": {"A": pa, "B": pb},
                "cross_condition_ctx_diff": cross,
                "metrics": m,
                "deltas": {
                    "unfit_removed": d_unfit, "fit_lost": d_fit,
                    "unclear": d_unclear, "unlabeled": d_unlab,
                    "ratio": ratio, "net_best": best, "net_worst": worst,
                },
            },
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )
    log(f"\n  → {out}")
    return 0


def dump_unlabeled(runs: dict, labels: dict, matrix: Path) -> int:
    """라벨이 필요한 행을 **조건을 가린 채** 낸다.

    어느 조건이 고른 행인지 보이면 라벨이 결론을 따라간다 — B 가 새로 고른 것에는
    후하게, A 만 고른 것에는 박하게 붙게 된다. 그래서 합집합을 `(context, code)` 로
    정렬해서만 내고, 조건도 회차 수도 찍지 않는다.
    """
    data = json.loads(matrix.read_text(encoding="utf-8"))
    body = {c["context_id"]: c["body"] for c in data["contexts"]}
    meaning = {
        (c["context_id"], x["code"]): x
        for c in data["contexts"] for x in c["candidates"]
    }
    # `matrix.json` 은 `description` 을 담지 않는다(유사도 행렬이 목적이라). 라벨의
    # 기준이 바로 그 `description` 이므로 시드 파일에서 읽어 함께 낸다 — 표시명만 보고
    # 붙이면 `labels.yaml` 머리말이 금지한 「어감으로 판정」을 하게 된다.
    seed = yaml.safe_load((ROOT / "data" / "keyword_preset.yaml").read_text(encoding="utf-8"))
    presets = {p["code"]: p for p in (seed.get("presets") or seed.get("keyword_presets") or [])}
    need = sorted({k for rs in runs.values() for r in rs for k in rows_of(r)} - set(labels))
    log(f"# 라벨이 필요한 행 {len(need)}건 — 조건 정보는 일부러 빼 두었다")
    log(f"# 기준은 tools/tau_grid/labels.yaml 머리말과 같다(프리셋 description 대조)")
    log()
    for cid, code in need:
        info = meaning.get((cid, code), {})
        p = presets.get(code, {})
        log(f"- context {cid} · {code}  (sim {info.get('sim', float('nan')):.4f})")
        log(f"    본문: {body.get(cid, '?')}")
        log(f"    의미: {p.get('display_name', '?')} — {p.get('description', '?')}")
        log(f"    예시: {' · '.join(p.get('examples', []))}")
        log()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

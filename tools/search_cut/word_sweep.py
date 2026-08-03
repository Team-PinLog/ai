"""단어형 · 문장형을 **한 표에** 놓고 `τ_abs × r` 격자를 훑는다 (S15P11A705-266).

**DB 도 GMS 도 부르지 않는다.** `word_grid.json`(단어형)과 `matrix.json`(문장형)을 읽어
임의의 `(τ_abs, r)` 를 재구성한다 — `sweep.py` 와 같은 원리이고, 다른 것은 **두 행렬을
동시에 읽는다**는 점이다.

이 티켓의 질문이 「한 값이 둘 다를 만족하는가, 아니면 질의 길이로 갈라야 하는가」이므로
두 표를 따로 내면 답이 안 나온다. 같은 행에서 단어형 이득과 문장형 손실을 함께 봐야
교환점이 보인다.

## 왜 `sweep.py` 를 그대로 쓰지 않았나

`sweep.py` 는 `labels.yaml` 을 요구한다(`attach_labels` 가 질의 전량의 라벨을 강제한다).
단어형은 (질의 × 소유자) 207행이라 손 라벨이 현실적이지 않고, **그럴 필요도 없다** —
이 티켓의 완료 조건은 「정답 누락 · 무관 통과 · 빈 결과」 셋이고 라벨이 필요한 것은
`sweep.py` 의 넷째 지표(꼬리 제거율)뿐이다.

**꼬리 제거율을 포기하는 대신 무관 통제를 45행으로 늘렸다.** 「무관한 것을 얼마나
잘랐나」를 라벨로 세는 대신 **정답이 없는 질의가 몇 건 침묵하는가**로 센다. 이쪽이
판정자 재량에 덜 기대고, `-213` 이 무관 질의 축에서 이미 쓴 방식이다.

## 정답 누락을 두 기준으로 센다

단어형은 기대 정답이 여럿이다(`신한` → 6건). 하나로 세면 기준이 자의적이 된다.

    miss_all       기대 정답이 **하나도** 안 남았다      사용자가 「안 나온다」고 말하는 상태
    miss_partial   기대 정답 중 **일부**가 사라졌다      덜 심각하지만 손실이다

`-213` 이 꼬리 제거를 비관·낙관 양쪽으로 낸 것과 같은 원칙이다.

## 회복 대상을 분모에서 가른다

계약이 못박은 것이다 — 「컷을 내려서 돌아오는 것」과 「내려도 안 돌아오는 것」을 섞으면
값을 못 정한다. `-255` 가 같은 「τ_abs 가 잘랐다」에 풀면 1위인 `스팟` 과 풀어도 8위인
`부캠` 이 함께 붙는 것을 보였다.

    recoverable   기대 정답 중 컷 전 순위가 `RECOVER_RANK` 안인 것   ← 컷으로 살릴 수 있다
    recovered     그중 이 조합에서 실제로 남은 것

`RECOVER_RANK=3` 의 근거는 `-255` 와 같다(`-191` top-3 12/12 · `-213` 「정답이 전부 3위
안」). 컷 전 8위인 것을 살리는 값은 **컷의 문제가 아니므로** 이 티켓이 답할 것이 아니다.

    python tools/search_cut/word_sweep.py
    python tools/search_cut/word_sweep.py --tau 0.20,0.24 --ratio 0.5,0.6
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

for _s in (sys.stdout, sys.stderr):
    # T28. 질의와 장소명을 찍는다 — 콘솔이 cp949 면 첫 표에서 죽는다.
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


def head(title: str) -> None:
    log("\n" + "=" * 96)
    log(title)
    log("=" * 96)


# 서비스가 쓰는 limit(personal-search.md §6.1, 공용 계약 08). 이 티켓은 건드리지 않는다.
SERVICE_LIMIT = 20

# `-255` 와 같은 값·같은 근거. 컷으로 살릴 수 있는 대상의 경계다.
RECOVER_RANK = 3


def cut(results: list[dict], tau: float, ratio: float, limit: int = SERVICE_LIMIT) -> list[dict]:
    """서비스가 하는 것을 그대로 재구성한다 — SQL `LIMIT` 이 먼저, 컷이 뒤다.

    `SearchService._cut` 을 `import` 하지 않고 다시 적었다(`-213` 이 세우고 `-255` 가
    따른 규칙). `import` 하면 구현이 명세와 달라도 둘이 함께 틀려 재구성이 「일치」한다.

    기준 top-1 은 **컷 전** 1위다. 컷 후 재계산하면 기준이 남은 것의 1위로 옮겨가
    아무것도 더 잘리지 않는다.
    """
    head_ = results[:limit]
    if not head_:
        return head_
    if tau <= 0 and ratio <= 0:
        return head_
    top = head_[0]["sim"]
    return [r for r in head_ if r["sim"] >= tau and r["sim"] >= ratio * top]


def eval_answered(rows: list[dict], tau: float, ratio: float) -> dict:
    """기대 정답이 있는 질의 집합. 누락·빈 결과·회복을 함께 센다."""
    miss_all = miss_partial = empty = 0
    recoverable = recovered = lost_top1 = 0
    lost = []
    for q in rows:
        base = q["results"][:SERVICE_LIMIT]
        kept = cut(q["results"], tau, ratio)
        kept_ids = {r["record_id"] for r in kept}
        want = [r for r in base if r["is_expected"]]
        if not want:
            # 컷 이전에 limit 이 잘랐다. 컷 값의 공로도 책임도 아니다.
            continue
        # **컷 전 1위인 정답을 잘랐는가.** 회복률보다 이쪽이 결정적이다 — 사용자가 보는
        # 것은 「순위가 밀렸다」가 아니라 「1위인데 0건이다」이고, 그것은 컷이 유사도
        # 순서를 존중하지 않는다는 뜻이다. 회복률 96% 뒤에 이 실패가 숨을 수 있어
        # 따로 센다.
        top1_hit = next((r for r in want if r["rank"] == 1), None)
        if top1_hit and top1_hit["record_id"] not in kept_ids:
            lost_top1 += 1
        alive = [r for r in want if r["record_id"] in kept_ids]
        if not alive:
            miss_all += 1
            lost.append({"query": q["query"], "as": q["as"],
                         "names": [r["name"] for r in want],
                         "sim": max(r["sim"] for r in want),
                         "rank": min(r["rank"] for r in want)})
        elif len(alive) < len(want):
            miss_partial += 1
        if not kept:
            empty += 1
        for r in want:
            if r["rank"] <= RECOVER_RANK:
                recoverable += 1
                if r["record_id"] in kept_ids:
                    recovered += 1
    return {
        "n": len(rows), "miss_all": miss_all, "miss_partial": miss_partial,
        "empty": empty, "recoverable": recoverable, "recovered": recovered,
        "lost_top1": lost_top1, "lost": lost,
    }


def eval_control(rows: list[dict], tau: float, ratio: float) -> dict:
    """정답이 없는 질의 집합. **0건이 좋은 것이다** — `-213` 의 무관 질의 축과 같다."""
    silenced = returned = 0
    for q in rows:
        kept = cut(q["results"], tau, ratio)
        returned += len(kept)
        if not kept:
            silenced += 1
    n = len(rows)
    return {"n": n, "silenced": silenced, "passed": n - silenced, "returned": returned,
            "silenced_pct": round(100 * silenced / n, 1) if n else 0.0}


def eval_sentence(data: dict, tau: float, ratio: float) -> dict:
    """문장형 회귀. `-213` 의 검증 질의 12건 + 무관 15건을 같은 규칙으로 다시 판정한다.

    **이 티켓의 주 리스크다.** 단어형을 살리려고 컷을 내리면 문장형에서 잃는 것이 무엇인지
    같은 행에서 보여야 한다.
    """
    miss = empty = 0
    lost = []
    for q in data["queries"]:
        base = q["results"][:SERVICE_LIMIT]
        kept = cut(q["results"], tau, ratio)
        kept_ids = {r["record_id"] for r in kept}
        want = [r for r in base if r["is_expected"]]
        if want and not any(r["record_id"] in kept_ids for r in want):
            miss += 1
            lost.append({"query": q["query"], "name": want[0]["name"], "sim": want[0]["sim"]})
        if not kept:
            empty += 1
    off = eval_control(data.get("offtopic", []), tau, ratio)
    return {"n": len(data["queries"]), "miss": miss, "empty": empty,
            "offtopic": off, "lost": lost}


def row_of(word: dict, sent: dict, tau: float, ratio: float) -> dict:
    w = eval_answered(word["queries"], tau, ratio)
    cross = eval_control(word["cross"], tau, ratio)
    off = eval_control(word["offtopic"], tau, ratio)
    s = eval_sentence(sent, tau, ratio)
    return {"tau_abs": tau, "ratio": ratio, "word": w, "cross": cross,
            "offtopic": off, "sentence": s}


def table(title: str, rows: list[dict]) -> None:
    head(title)
    log(f"  {'τ_abs':>6} {'r':>5} │ {'단어 누락':>9} {'1위손실':>7} {'빈결과':>6} {'회복':>9} │ "
        f"{'무관통과':>9} {'교차통과':>9} │ {'문장 누락':>9} {'문장무관':>9}")
    log(f"  {'-' * 106}")
    for r in rows:
        w, s = r["word"], r["sentence"]
        rec = f"{w['recovered']}/{w['recoverable']}"
        log(
            f"  {r['tau_abs']:>6.3f} {r['ratio']:>5.2f} │ "
            f"{w['miss_all']:>4}/{w['n']:<4} {w['lost_top1']:>7} {w['empty']:>6} {rec:>9} │ "
            f"{r['offtopic']['passed']:>4}/{r['offtopic']['n']:<4} "
            f"{r['cross']['passed']:>4}/{r['cross']['n']:<4} │ "
            f"{s['miss']:>4}/{s['n']:<4} "
            f"{s['offtopic']['passed']:>4}/{s['offtopic']['n']:<4}"
        )


def split_table(word: dict, sent: dict, taus: list[float]) -> None:
    """**질의 길이로 τ_abs 를 가르면** 무엇이 달라지는가. 계약이 요구한 셋째 답이다.

    단일값은 두 대역을 한 칼로 자르지만 두 대역이 겹치지 않는다면(단어형 정답 top-3 하한
    0.2438 · 문장형 정답 하한 0.3642) 가르는 쪽이 양쪽에서 동시에 낫다. 그것이 실제로
    성립하는지를 숫자로 낸다.

    **경계 정의는 이 데이터로 확정할 수 없다.** 여기 단어형은 전부 공백 없는 단일 어절
    (2~5자)이고 문장형은 전부 공백 포함 6자 이상이라, 「글자 수」와 「어절 수」 두 정의가
    같은 답을 낸다. `-255` 의 `신한 부트캠프`(7자 2어절)처럼 둘이 갈리는 질의가 이
    행렬에 없다. 그래서 표는 **가르는 것의 이득**만 답하고 경계값은 후속으로 남긴다.
    """
    head("질의 길이로 τ_abs 를 가르면 — 단일값과 비교")
    log("  경계: 단어형(공백 없는 2~5자) vs 문장형(공백 포함 6자↑). 이 행렬에서 두 정의는")
    log("  같은 답을 낸다 — 둘이 갈리는 질의(`신한 부트캠프` 류)가 없기 때문이다.\n")
    r = word["cut"]["ratio"]
    log(f"  {'τ(단어)':>8} {'τ(문장)':>8} │ {'단어 회복':>10} {'단어 누락':>10} "
        f"{'단어무관':>9} │ {'문장 누락':>10} {'문장무관':>9}")
    log(f"  {'-' * 86}")
    for t_word in [t for t in taus if 0.18 <= t <= 0.30]:
        for t_sent in (t_word, 0.30, 0.34):
            w = eval_answered(word["queries"], t_word, r)
            off = eval_control(word["offtopic"], t_word, r)
            s = eval_sentence(sent, t_sent, r)
            mark = "  ← 단일값" if t_sent == t_word else ""
            log(f"  {t_word:>8.3f} {t_sent:>8.3f} │ "
                f"{w['recovered']:>5}/{w['recoverable']:<4} {w['miss_all']:>5}/{w['n']:<4} "
                f"{off['passed']:>4}/{off['n']:<4} │ "
                f"{s['miss']:>5}/{s['n']:<4} "
                f"{s['offtopic']['passed']:>4}/{s['offtopic']['n']:<4}{mark}")
        log()


def distribution(word: dict, sent: dict) -> dict:
    """격자보다 먼저 대역을 찍는다. **이 티켓의 결론을 대역이 지배한다.**"""
    head("분포 — 단어형과 문장형이 다른 대역에 있는가")

    def band(name: str, xs: list[float]) -> dict:
        if not xs:
            log(f"  {name:<28} n=0")
            return {"n": 0}
        xs = sorted(xs)
        q = lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731
        log(f"  {name:<28} n={len(xs):<5} min={xs[0]:.4f} p25={q(.25):.4f} "
            f"p50={q(.50):.4f} p75={q(.75):.4f} max={xs[-1]:.4f}")
        return {"n": len(xs), "min": xs[0], "p25": q(.25), "p50": q(.50),
                "p75": q(.75), "max": xs[-1]}

    w_hit = [x["sim"] for q in word["queries"] for x in q["results"] if x["is_expected"]]
    w_hit3 = [x["sim"] for q in word["queries"] for x in q["results"]
              if x["is_expected"] and x["rank"] <= RECOVER_RANK]
    w_off = [q["results"][0]["sim"] for q in word["offtopic"] if q["results"]]
    w_cross = [q["results"][0]["sim"] for q in word["cross"] if q["results"]]
    s_hit = [x["sim"] for q in sent["queries"] for x in q["results"] if x["is_expected"]]
    s_off = [q["results"][0]["sim"] for q in sent.get("offtopic", []) if q["results"]]

    out = {
        "word_hit": band("단어형 정답 전량", w_hit),
        "word_hit_top3": band(f"단어형 정답 top-{RECOVER_RANK}", w_hit3),
        "word_offtopic_top1": band("단어형 무관 top-1", w_off),
        "word_cross_top1": band("단어형 교차 top-1", w_cross),
        "sentence_hit": band("문장형 정답", s_hit),
        "sentence_offtopic_top1": band("문장형 무관 top-1", s_off),
    }

    log()
    log("  τ_abs 를 정하는 것은 **정답 하한과 무관 상한의 간격**이다.")
    for label, hit, off in (
        ("단어형 전량", w_hit, w_off),
        (f"단어형 top-{RECOVER_RANK}", w_hit3, w_off),
        ("문장형", s_hit, s_off),
    ):
        gap = min(hit) - max(off)
        log(f"    {label:<14} 정답 하한 {min(hit):.4f}  vs  무관 상한 {max(off):.4f}   "
            f"간격 {gap:+.4f}" + ("   역전" if gap < 0 else "   가를 수 있다"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--word", default=str(ROOT / ".search" / "word_grid.json"))
    ap.add_argument("--matrix", default=str(ROOT / ".search" / "matrix.json"))
    ap.add_argument("--tau", default="", help="비우면 0.00 및 0.14~0.36")
    ap.add_argument("--ratio", default="", help="비우면 0.00 및 0.30~0.80")
    ap.add_argument("--out", default=str(ROOT / ".search" / "word_sweep.json"))
    args = ap.parse_args()

    for p in (Path(args.word), Path(args.matrix)):
        if not p.exists():
            raise SystemExit(f"{p} 가 없다. word_matrix.py · matrix.py 를 먼저 돌려라.")
    word = json.loads(Path(args.word).read_text(encoding="utf-8"))
    sent = json.loads(Path(args.matrix).read_text(encoding="utf-8"))

    # 격자를 아래로 넓히고 **0.01 간격으로 촘촘히** 잡는다. `-213` 은 0.20~0.44 를 0.02
    # 로 훑었고 단어형 정답은 그 아래 대역에 있다(`-255`: 본문에 있는 2자가 0.24 대역).
    # 간격을 좁힌 이유는 채택 후보가 데이터점 하나에 붙기 때문이다 — 단어형 정답 top-3
    # 최솟값이 0.2438 이라 0.02 격자에서는 0.24 와 0.26 사이가 통째로 안 보인다.
    taus = ([float(x) for x in args.tau.split(",") if x.strip()]
            or [0.0] + [round(0.16 + 0.01 * i, 3) for i in range(19)])
    ratios = ([float(x) for x in args.ratio.split(",") if x.strip()]
              or [0.0] + [round(0.30 + 0.05 * i, 3) for i in range(11)])

    log(f"  단어형  profile={word['profile']} · 정답 {word['word_count']}행 "
        f"· 교차 {word['cross_count']}행 · 무관 {word['offtopic_count']}행")
    log(f"  문장형  질의 {sent['query_count']}건 · 무관 {sent['offtopic_count']}행 "
        f"({Path(args.matrix).name}, S15P11A705-213)")

    dist = distribution(word, sent)

    tau_rows = [row_of(word, sent, t, 0.0) for t in taus]
    table("τ_abs 단독 — r=0", tau_rows)
    ratio_rows = [row_of(word, sent, 0.0, r) for r in ratios]
    table("r 단독 — τ_abs=0", ratio_rows)

    # 현행값과 그 주변. 「지금 무엇을 잃고 있는가」가 채택 판단의 기준선이다.
    current = row_of(word, sent, sent_tau := word["cut"]["tau_abs"], word["cut"]["ratio"])
    table(f"현행값 τ_abs={sent_tau} · r={word['cut']['ratio']}", [current])
    head("현행값에서 잃고 있는 단어형 정답")
    for d in current["word"]["lost"][:40]:
        log(f"    「{d['query']}」 as={d['as']:<10} {','.join(d['names'])[:34]:<36} "
            f"최고 {d['sim']:.4f} ({d['rank']}위)")
    log(f"    … 총 {len(current['word']['lost'])}건")

    combo = [row_of(word, sent, t, r) for t in taus for r in ratios]

    safe = [c for c in combo if c["sentence"]["miss"] == 0 and c["sentence"]["empty"] == 0]
    log(f"\n  문장형 정답 누락 0 · 빈 결과 0 인 조합: {len(safe)}/{len(combo)}")
    if len(safe) == len(combo):
        # **이 경우 「문장형 회귀」는 제약이 되지 못한다.** 격자 전체가 통과하므로 조합을
        # 가리지 못하고, 그러면 문장형에서 실제로 잃는 것을 다른 열에서 찾아야 한다.
        log("  격자 전체가 통과한다 — 이 축은 조합을 가르지 못한다. 문장형 손실은")
        log("  「정답 누락」이 아니라 「무관 질의 침묵」 열에서 봐야 한다(맨 오른쪽).")

    # 이 티켓의 목적은 **단어형 회복**이고 무관 통과가 그 대가다. 회복을 목적함수로 두고
    # 같은 회복에서 무관 통과가 적은 것을 위로 올린다. 반대로 정렬하면 τ_abs 를 **올려**
    # 무관을 죽이는 조합이 이기는데, 그것은 이 티켓이 답하려는 질문이 아니다.
    #
    # **1위 손실을 회복률보다 앞에 둔다.** 「컷 전 1위인 정답이 0건이 된다」는 회복률
    # 96% 뒤에 숨을 수 있고, 사용자가 보는 실패는 그쪽이 더 크다.
    ranked = sorted(safe, key=lambda c: (c["word"]["lost_top1"], -c["word"]["recovered"],
                                         c["offtopic"]["passed"],
                                         c["sentence"]["offtopic"]["passed"], -c["tau_abs"]))
    table("1위 손실 0 우선 · 회복 최대 · 무관 통과 최소 — 상위 12", ranked[:12])

    head("교환점 — 현행(τ_abs=0.30) 대비 증분")
    log(f"  {'τ_abs':>6} {'r':>5} {'회복':>7} {'단어무관':>9} {'문장무관':>9} {'교환비':>8}"
        "   교환비 = 무관 통과 증가 ÷ 회복 증가")
    base = row_of(word, sent, word["cut"]["tau_abs"], word["cut"]["ratio"])
    b_rec = base["word"]["recovered"]
    b_off = base["offtopic"]["passed"]
    b_soff = base["sentence"]["offtopic"]["passed"]
    for t in taus:
        c = row_of(word, sent, t, word["cut"]["ratio"])
        d_rec = c["word"]["recovered"] - b_rec
        d_off = c["offtopic"]["passed"] - b_off
        rate = f"{d_off / d_rec:.2f}" if d_rec > 0 else "—"
        log(f"  {t:>6.3f} {word['cut']['ratio']:>5.2f} {d_rec:>+7} {d_off:>+9} "
            f"{c['sentence']['offtopic']['passed'] - b_soff:>+9} {rate:>8}")

    split_table(word, sent, taus)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"ticket": "S15P11A705-266", "distribution": dist,
         "current": current, "tau_only": tau_rows, "ratio_only": ratio_rows,
         "combo": combo}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

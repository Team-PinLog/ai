"""단어형 판정의 두 정의를 규칙별로 훑는다 (S15P11A705-273).

`boundary_matrix.py` 가 굳힌 행렬과 `-266` 의 `word_grid.json` 을 **함께** 읽는다.
따로 내면 「1어절 대역에서 옳은 규칙이 2어절 대역에서도 옳은가」에 답할 수 없다.

**DB 도 GMS 도 부르지 않는다.** 규칙을 바꿔 다시 훑는 일이 잦으므로 임베딩과 판정을
갈라 두는 것이 `-213` 이래의 구조다.

## 무엇을 비교하는가

`_is_word_query` 를 바꿔 끼울 수 있는 자리로 보고, 정의 여섯을 같은 행렬에 건다.

    current            공백 없음 AND 원문 ≤5자          현행
    chars_only         원문 ≤5자                        「글자 수」 정의
    chars_nospace      공백 뗀 길이 ≤5자                「글자 수」를 공백 제외로 읽으면
    words_only         1어절                            「어절 수」 정의
    words_le2          ≤2어절                           어절 수를 느슨하게
    words2_chars8      ≤2어절 AND 공백 뗀 길이 ≤8자     두 축을 함께, 경계를 넓혀

## 컷은 다시 적는다

`SearchService._cut` 을 `import` 하지 않는다 — 구현이 명세와 달라도 둘이 함께 틀려
재구성이 「일치」한다(`-213` 이 세운 규칙, `-255`·`-266` 이 따랐다).

## 짝 대조가 이 티켓의 본론이다

같은 쌍에서 나온 `spaced`/`joined` 는 **기대 정답이 같다.** 그래서 둘 사이의 차이는
전부 「공백 하나」에 귀속된다. 두 가지를 나눠 본다.

    Δsim      공백을 떼면 임베딩 유사도 자체가 움직이는가   (모델의 성질)
    Δ판정      규칙이 다른 하한을 물려 결과가 갈리는가       (우리 코드의 성질)

**둘을 합치면 원인이 섞인다.** 공백을 떼서 결과가 좋아졌다면 그것이 모델 때문인지
하한이 느슨해져서인지 갈려야 처방이 나온다.

## 초점 집합 — 재량을 결과 옆에 둔다

전량에는 기능어 쌍(`같이 가서`·`거의 없고`)이 섞인다. 본문에서 기계로 뽑았으므로
「정답 있는 질의」로 세지만 사용자가 그 말로 검색하지는 않는다. `-266` 은 사람이 골라
이 문제를 피했고 **그 선정이 재량이라고 스스로 적었다.**

여기서는 전량을 재고 `--focus` 로 부분집합 지표를 **나란히** 낸다. 재량을 없애는 대신
재량의 영향을 눈에 보이게 두는 쪽을 골랐다.

    python tools/search_cut/boundary_sweep.py
    python tools/search_cut/boundary_sweep.py --focus     # 초점 집합 지표를 함께
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


# 서비스 기본값. 행렬의 `cut` 이 있으면 그쪽을 쓴다.
TAU_SENT = 0.30
TAU_WORD = 0.24
RATIO = 0.60
LIMIT = 20
MAX_CHARS = 5

# 회복 경계. `-255`·`-266` 과 같은 3위 — 이 데이터셋에서 정답이 전부 3위 안에 있다는
# `-191`·`-213` 의 기준선을 따른다.
RECOVER_RANK = 3

# 초점 집합. **이것이 이 스크립트의 유일한 재량이다.**
# 기준: 두 어절 모두 내용어(명사·고유명사)이고, 사용자가 그 말로 검색할 법한 쌍.
# 전량 지표 옆에 두는 것이 목적이므로 좁게 잡는다.
FOCUS_PAIRS: tuple[tuple[str, str], ...] = (
    ("그네", "공원"),
    ("신한", "부캠"),
    ("신한", "부트캠프"),
    ("우주", "컨셉"),
    ("치킨", "난반"),
    ("양갱", "파는"),
    ("비건", "샌드위치"),
    ("돼지국밥", "먹었음"),
    ("무한도전", "방영된"),
    ("연어오차즈케", "치킨"),
    ("트러플감자전", "짱맛"),
    ("마늘보쌈과", "낙지전골을"),
    ("아이스크림", "할인점도"),
    ("그네팟", "스팟"),
    ("노트북", "들고"),
    ("빗소리", "들으면서"),
)


# ── 판정 규칙 ────────────────────────────────────────────────────────────────
RULES = {
    "current": lambda e: (not e["has_space"]) and e["chars"] <= MAX_CHARS,
    "chars_only": lambda e: e["chars"] <= MAX_CHARS,
    "chars_nospace": lambda e: e["chars_nospace"] <= MAX_CHARS,
    "words_only": lambda e: e["words"] == 1,
    "words_le2": lambda e: e["words"] <= 2,
    "words2_chars8": lambda e: e["words"] <= 2 and e["chars_nospace"] <= 8,
}


def cut(results: list[dict], is_word: bool, tau_word: float, tau_sent: float,
        ratio: float, limit: int) -> list[dict]:
    """`SearchService._cut` 의 재구성. LIMIT 이 먼저, 컷이 뒤다.

    비상 스위치 가드(둘 다 0 이면 컷 전체가 꺼진다)를 분기보다 **앞**에 둔 것까지
    같다 — 구현이 그 순서를 안전장치로 명시했다.
    """
    rows = results[:limit]
    if not rows:
        return rows
    if tau_sent <= 0 and ratio <= 0:
        return rows
    floor = tau_word if is_word else tau_sent
    top = rows[0]["sim"]
    return [r for r in rows if r["sim"] >= floor and r["sim"] >= ratio * top]


def load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"행렬이 없다: {path}\nboundary_matrix.py 를 먼저 돌려라.")
    return json.loads(path.read_text(encoding="utf-8"))


# ── 지표 ────────────────────────────────────────────────────────────────────
def score(word_rows: list[dict], cross_rows: list[dict], off_rows: list[dict],
          rule, tau_word: float, tau_sent: float, ratio: float,
          limit: int = LIMIT) -> dict:
    miss = lost_top = empty = 0
    recovered = recoverable = 0
    for e in word_rows:
        is_word = rule(e)
        kept = cut(e["results"], is_word, tau_word, tau_sent, ratio, limit)
        kept_ids = {r["record_id"] for r in kept}
        pre = e["results"][:limit]
        hits_pre = [r for r in pre if r["is_expected"]]
        if not hits_pre:
            # 컷 전 limit 안에 정답이 없다. 컷의 책임이 아니다 — 분모에서 뺀다.
            continue
        if not any(r["is_expected"] for r in kept):
            miss += 1
        if not kept:
            empty += 1
        if pre and pre[0]["is_expected"] and not kept:
            lost_top += 1
        for r in hits_pre:
            if r["rank"] <= RECOVER_RANK:
                recoverable += 1
                if r["record_id"] in kept_ids:
                    recovered += 1
    cross_pass = sum(
        1 for e in cross_rows
        if cut(e["results"], rule(e), tau_word, tau_sent, ratio, limit)
    )
    off_pass = sum(
        1 for e in off_rows
        if cut(e["results"], rule(e), tau_word, tau_sent, ratio, limit)
    )
    return {
        "miss": miss,
        "lost_top": lost_top,
        "empty": empty,
        "recovered": recovered,
        "recoverable": recoverable,
        "cross_pass": cross_pass,
        "cross_total": len(cross_rows),
        "off_pass": off_pass,
        "off_total": len(off_rows),
        "word_total": sum(
            1 for e in word_rows
            if any(r["is_expected"] for r in e["results"][:limit])
        ),
    }


def best_hit(entry: dict) -> dict | None:
    for r in entry["results"]:
        if r["is_expected"]:
            return r
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", default=str(ROOT / ".search" / "boundary_grid.json"))
    ap.add_argument("--word-grid", default=str(ROOT / ".search" / "word_grid.json"))
    ap.add_argument("--focus", action="store_true", help="초점 집합 지표를 함께 낸다")
    args = ap.parse_args()

    g = load(Path(args.grid))
    cutcfg = g.get("cut", {})
    tau_sent = float(cutcfg.get("tau_abs", TAU_SENT))
    tau_word = float(cutcfg.get("tau_word", TAU_WORD))
    ratio = float(cutcfg.get("ratio", RATIO))
    max_chars = int(cutcfg.get("word_max_chars", MAX_CHARS))
    globals()["MAX_CHARS"] = max_chars

    log(f"  행렬 {Path(args.grid).name}  쌍 {g['pair_count']}종 "
        f"· 정답 {g['word_count']}행 · 교차 {g['cross_count']}행 "
        f"· 무관 {g['offtopic_count']}행")
    log(f"  컷 τ_sent={tau_sent} · τ_word={tau_word} · r={ratio} "
        f"· 경계 {max_chars}자 · limit={LIMIT}\n")

    W, X, OFF = g["queries"], g["cross"], g["offtopic"]

    # ── ① 대역 분포 ────────────────────────────────────────────────────────
    log("## 대역 — 현행 규칙이 무엇을 어디로 보내는가\n")
    bands: dict[str, list[dict]] = {"A": [], "B": [], "C": [], "D": []}
    for e in W:
        short = e["chars"] <= max_chars
        key = ("A" if (not e["has_space"] and short)
               else "B" if (e["has_space"] and short)
               else "C" if (not e["has_space"] and not short) else "D")
        bands[key].append(e)
    log("| 대역 | | 행 | 현행 하한 | 글자 수 정의 | 어절 수 정의 |")
    log("|---|---|---|---|---|---|")
    for key, label, cur, ch, wd in (
        ("A", "무공백·짧다", "0.24", "0.24", "0.24"),
        ("B", "공백·짧다", "0.30", "**0.24**", "0.30"),
        ("C", "무공백·길다", "0.30", "0.30", "**0.24**"),
        ("D", "공백·길다", "0.30", "0.30", "0.30"),
    ):
        log(f"| {key} | {label} | {len(bands[key])} | {cur} | {ch} | {wd} |")
    log()

    # ── ② 규칙 격자 ────────────────────────────────────────────────────────
    log("## 규칙별 지표 — 같은 행렬, 다른 `_is_word_query`\n")
    log("| 규칙 | 정답 누락 | 1위 손실 | 빈 결과 | 회복 | 무관 통과 | 교차 통과 |")
    log("|---|---|---|---|---|---|---|")
    scores = {}
    for name, rule in RULES.items():
        s = score(W, X, OFF, rule, tau_word, tau_sent, ratio)
        scores[name] = s
        mark = "**" if name == "current" else ""
        log(f"| {mark}{name}{mark} | {s['miss']}/{s['word_total']} | {s['lost_top']} "
            f"| {s['empty']} | {s['recovered']}/{s['recoverable']} "
            f"| {s['off_pass']}/{s['off_total']} | {s['cross_pass']}/{s['cross_total']} |")
    # 컷 없음 기준선
    s0 = score(W, X, OFF, lambda e: True, 0.0, 0.0, 0.0)
    log(f"| (컷 없음) | {s0['miss']}/{s0['word_total']} | {s0['lost_top']} | {s0['empty']} "
        f"| {s0['recovered']}/{s0['recoverable']} | {s0['off_pass']}/{s0['off_total']} "
        f"| {s0['cross_pass']}/{s0['cross_total']} |")
    log()

    # ── ②' 대역별 — 손실이 어느 대역에 있는가 ───────────────────────────────
    log("## 대역별 손실 — 현행 규칙\n")
    log("규칙 전체 지표는 손실이 **어느 대역에서** 나는지 감춘다. B·C 가 이 티켓이"
        " 만든 대역이므로 그 둘이 따로 보여야 한다.\n")
    log("| 대역 | 행 | 정답 누락 | 1위 손실 | 빈 결과 | 회복 |")
    log("|---|---|---|---|---|---|")
    for key in ("A", "B", "C", "D"):
        s = score(bands[key], [], [], RULES["current"], tau_word, tau_sent, ratio)
        log(f"| {key} | {len(bands[key])} | {s['miss']}/{s['word_total']} "
            f"| {s['lost_top']} | {s['empty']} | {s['recovered']}/{s['recoverable']} |")
    log()
    log("같은 대역을 **어절 수 정의**로 다시 보면 — B·C 의 하한만 바뀐다.\n")
    log("| 대역 | 행 | 정답 누락 | 1위 손실 | 빈 결과 | 회복 |")
    log("|---|---|---|---|---|---|")
    for key in ("A", "B", "C", "D"):
        s = score(bands[key], [], [], RULES["words_only"], tau_word, tau_sent, ratio)
        log(f"| {key} | {len(bands[key])} | {s['miss']}/{s['word_total']} "
            f"| {s['lost_top']} | {s['empty']} | {s['recovered']}/{s['recoverable']} |")
    log()

    # ── ③ 짝 대조 ──────────────────────────────────────────────────────────
    log("## 짝 대조 — 공백 하나가 무엇을 바꾸는가\n")
    by_key: dict[tuple, dict] = {}
    for e in W:
        by_key[(tuple(e["pair"]), e["as"], e["form"])] = e
    pairs_seen = sorted({(tuple(e["pair"]), e["as"]) for e in W})

    deltas = []
    verdict_split = []   # 현행 규칙에서 하한이 갈리는 짝
    result_split = []    # 실제 결과(정답 포함 여부)가 갈리는 짝
    for pair, who in pairs_seen:
        sp = by_key.get((pair, who, "spaced"))
        jo = by_key.get((pair, who, "joined"))
        if not sp or not jo:
            continue
        hs, hj = best_hit(sp), best_hit(jo)
        if hs and hj:
            deltas.append(hj["sim"] - hs["sim"])
        ws, wj = RULES["current"](sp), RULES["current"](jo)
        if ws != wj:
            verdict_split.append((pair, who, sp, jo))
        keep_s = cut(sp["results"], ws, tau_word, tau_sent, ratio, LIMIT)
        keep_j = cut(jo["results"], wj, tau_word, tau_sent, ratio, LIMIT)
        ok_s = any(r["is_expected"] for r in keep_s)
        ok_j = any(r["is_expected"] for r in keep_j)
        if ok_s != ok_j:
            result_split.append((pair, who, ok_s, ok_j, hs, hj))

    if deltas:
        log(f"임베딩 자체의 이동 — `joined` 정답 유사도 − `spaced` 정답 유사도 "
            f"({len(deltas)}짝)\n")
        log("```")
        log(f"평균 {statistics.mean(deltas):+.4f}   중앙값 {statistics.median(deltas):+.4f}")
        log(f"범위 {min(deltas):+.4f} ~ {max(deltas):+.4f}")
        log(f"joined 가 더 높은 짝 {sum(1 for d in deltas if d > 0)}/{len(deltas)}")
        log("```\n")

    log(f"현행 규칙에서 **하한이 갈리는 짝** {len(verdict_split)}건 "
        f"— 공백을 떼면 0.24, 두면 0.30\n")
    log(f"그중 **결과가 실제로 갈리는 짝** {len(result_split)}건\n")
    if result_split:
        log("| 쌍 | 소유자 | spaced 정답 | joined 정답 | spaced sim | joined sim |")
        log("|---|---|---|---|---|---|")
        for pair, who, ok_s, ok_j, hs, hj in result_split[:40]:
            log(f"| {pair[0]} {pair[1]} | {who} | {'포함' if ok_s else '**누락**'} "
                f"| {'포함' if ok_j else '**누락**'} "
                f"| {hs['sim']:.4f}({hs['rank']}위) | {hj['sim']:.4f}({hj['rank']}위) |")
        if len(result_split) > 40:
            log(f"\n… {len(result_split) - 40}건 더")
    log()

    # ── ④ 경계 — 몇 자부터인가 ───────────────────────────────────────────────
    log("## 경계 — 글자 수와 어절 수를 분리해서\n")
    log("정답 유사도(그 행의 최고 정답)의 분포. **두 하한과 나란히 읽는다.**\n")

    def band_table(rows: list[dict], keyf, label: str) -> None:
        buckets: dict[int, list[float]] = {}
        for e in rows:
            h = best_hit(e)
            if not h:
                continue
            buckets.setdefault(keyf(e), []).append(h["sim"])
        log(f"### {label}\n")
        log("| 값 | 행 | 최솟값 | 중앙값 | 최댓값 | ≥0.30 | ≥0.24 |")
        log("|---|---|---|---|---|---|---|")
        for k in sorted(buckets):
            v = sorted(buckets[k])
            log(f"| {k} | {len(v)} | {v[0]:.4f} | {statistics.median(v):.4f} "
                f"| {v[-1]:.4f} | {sum(1 for x in v if x >= tau_sent)}/{len(v)} "
                f"| {sum(1 for x in v if x >= tau_word)}/{len(v)} |")
        log()

    band_table(W, lambda e: e["chars"], "글자 수(원문, 공백 포함)")
    band_table(W, lambda e: e["chars_nospace"], "글자 수(공백 제외)")
    band_table(W, lambda e: e["words"], "어절 수")
    band_table([e for e in W if e["words"] == 1], lambda e: e["chars"],
               "1어절만 — 글자 수")
    band_table([e for e in W if e["words"] == 2], lambda e: e["chars_nospace"],
               "2어절만 — 공백 제외 글자 수")

    # ── ⑤ `-266` 의 1어절 행렬과 나란히 ────────────────────────────────────
    wg = Path(args.word_grid)
    if wg.exists():
        w = json.loads(wg.read_text(encoding="utf-8"))
        log("## `-266` 의 1어절 행렬 — 같은 표에 놓는다\n")
        rows = [dict(e, has_space=False, chars_nospace=e["chars"], words=1,
                     form="single", pair=[e["query"]])
                for e in w["queries"]]
        crossw = [dict(e, has_space=False, chars_nospace=e["chars"], words=1,
                       form="single", pair=[e["query"]])
                  for e in w["cross"]]
        offw = [dict(e, has_space=False, chars_nospace=e["chars"], words=1,
                     form="single", pair=[e["query"]])
                for e in w["offtopic"]]
        log("| 규칙 | 정답 누락 | 1위 손실 | 빈 결과 | 회복 | 무관 통과 |")
        log("|---|---|---|---|---|---|")
        for name in ("current", "chars_only", "words_only"):
            s = score(rows, crossw, offw, RULES[name], tau_word, tau_sent, ratio)
            log(f"| {name} | {s['miss']}/{s['word_total']} | {s['lost_top']} "
                f"| {s['empty']} | {s['recovered']}/{s['recoverable']} "
                f"| {s['off_pass']}/{s['off_total']} |")
        log()
        band_table(rows, lambda e: e["chars"], "1어절 질의 — 글자 수 (`-266` 행렬)")

    # ── ⑥ 초점 집합 ────────────────────────────────────────────────────────
    if args.focus:
        focus = {tuple(p) for p in FOCUS_PAIRS}
        FW = [e for e in W if tuple(e["pair"]) in focus]
        FX = [e for e in X if tuple(e["pair"]) in focus]
        log("## 초점 집합 — 재량을 결과 옆에 둔다\n")
        log(f"쌍 {len(focus)}종 · 정답 {len(FW)}행 · 교차 {len(FX)}행. "
            f"기준은 소스 `FOCUS_PAIRS` 주석에 있다.\n")
        missing = focus - {tuple(e["pair"]) for e in W} - {tuple(e["pair"]) for e in X}
        if missing:
            log(f"**행렬에 없는 초점 쌍 {len(missing)}건**: "
                f"{sorted(' '.join(m) for m in missing)}\n")
        log("| 규칙 | 정답 누락 | 1위 손실 | 빈 결과 | 회복 | 교차 통과 |")
        log("|---|---|---|---|---|---|")
        for name, rule in RULES.items():
            s = score(FW, FX, [], rule, tau_word, tau_sent, ratio)
            log(f"| {name} | {s['miss']}/{s['word_total']} | {s['lost_top']} "
                f"| {s['empty']} | {s['recovered']}/{s['recoverable']} "
                f"| {s['cross_pass']}/{s['cross_total']} |")
        log()
        log("### 초점 짝의 실제 결과\n")
        log("| 질의 | 자 | 어절 | 소유자 | 현행 하한 | 정답 sim(순위) | 컷 후 |")
        log("|---|---|---|---|---|---|---|")
        for e in sorted(FW, key=lambda x: (x["pair"], x["as"], x["form"])):
            h = best_hit(e)
            isw = RULES["current"](e)
            kept = cut(e["results"], isw, tau_word, tau_sent, ratio, LIMIT)
            ok = any(r["is_expected"] for r in kept)
            log(f"| {e['query']} | {e['chars']} | {e['words']} | {e['as']} "
                f"| {tau_word if isw else tau_sent} "
                f"| {h['sim']:.4f}({h['rank']}위) | "
                f"{'포함' if ok else '**누락**'} ({len(kept)}건) |")
        log()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

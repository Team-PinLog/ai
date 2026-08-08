"""검색 **순위** 지표 baseline — P48 0단계.

기존 하네스(`sweep.py` · `word_sweep.py`)의 지표는 전부 **컷 기준**이다
(`eval_answered` 는 정답이 잘렸는지, `eval_control` 은 무관 질의가 침묵했는지).
컷은 순위를 바꾸지 않고 자르기만 하므로 그것으로 충분했다.

P48 1단계 이후는 **순위를 바꾸는 변경**이다. 정답이 6위에서 2위로 올라와도 컷이 그대로면
`kept` 값이 변하지 않아 **개선이 0으로 보인다.** 그래서 순위 지표를 따로 둔다.

이 스크립트는 **DB 도 GMS 도 부르지 않는다.** 기존 행렬 셋만 읽는다 — 행마다 `rank` 가
이미 들어 있으므로(`matrix.py` · `word_matrix.py` · `recall_probe.py`) 세기만 하면 된다.

    python tools/search_cut/rank_score.py                 # 표 출력
    python tools/search_cut/rank_score.py --json OUT      # baseline 보존용 JSON

## 무엇을 가르는가

**컷 전 / 컷 후를 나눈다.** 컷 후만 보면 순위 개선이 가려진다(위 문단). 컷 전만 보면
사용자가 실제로 보는 것과 무관해진다. 둘 다 필요하다.

**단어형 / 문장형을 나눈다.** 두 대역이 겹치지 않는다(`S15P11A705-266` — 문장형 정답
하한과 단어형 정답 하한 사이에 간격이 있다). 합산 평균은 두 대역의 중간값을 만들어
**어느 쪽도 설명하지 못한다.** P48 채택 조건도 합산 판정을 금지한다.

**정답 있는 질의 / 무관 질의를 나눈다.** 무관 질의에서 좋은 것은 0건 반환이고, 정답 있는
질의에서 좋은 것은 그 반대다. 한 지표에 섞으면 방향이 상쇄된다.

## 왜 MRR 만으로 판정하지 않는가

단어형 격자는 정답을 손으로 짝짓지 않고 **본문 문자열 포함으로 계산**하므로
(`word_matrix.py`) 한 질의의 정답이 여럿이다(`expect_count` ≥ 2 인 행이 있다).
MRR 은 **첫 정답만** 보므로 나머지가 어디에 있든 값이 같다.

    정답 3개가 1·2·3위     MRR = 1.0
    정답 3개가 1·9·14위    MRR = 1.0      ← 명백히 나쁜데 같은 값

그래서 `Recall@k` 와 `nDCG@k` 를 함께 낸다. 셋의 역할이 다르다.

    Hit@k      상위 k 안에 정답이 하나라도 있는가       — 사용자 체감에 가장 가깝다
    Recall@k   전체 정답 중 몇 개가 상위 k 에 들어왔나  — 복수 정답을 센다
    MRR        첫 정답이 얼마나 위인가                  — 단일 정답에서 민감하다
    nDCG@k     정답들이 얼마나 위에 몰려 있나           — 순서까지 본다

## 가드 — 계산하지 못하는 것을 통과로 만들지 않는다

`check_docs_index.py` 와 같은 태도다. 다음이면 **수치를 내지 않고 실패한다.**

    profile 불일치        서로 다른 Profile 의 행렬을 한 표에 놓으면 비교가 성립하지 않는다
    필수 필드 누락        `rank` · `sim` 이 없으면 순위를 셀 수 없다
    정답 질의에 정답 0건  라벨이 어긋난 것이다. 분모에서 조용히 빠지면 지표가 부풀려진다
    무관 질의에 정답 존재  그 행은 통제가 아니라 정답 있는 질의다(`word_matrix.py` 와 같은 가드)

## 결정성

입력이 파일뿐이고 난수도 시각도 쓰지 않으므로 **같은 입력에 같은 출력**이다. `--json` 은
입력 파일의 SHA-256 을 함께 적는다 — 두 번 돌려 나온 JSON 이 같은지로 결정성을,
해시로 어떤 행렬을 읽었는지를 확인한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEARCH = ROOT / ".search"

# cp949 콘솔에서 `—` 한 글자에 죽지 않게 한다(T28·T77). 호출자가 PYTHONIOENCODING 을
# 기억해야 하는 상태를 남기지 않는다 — `keyword_matrix.py` 와 같은 방어다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 정본은 `app/core/config.py` 다. 하네스가 앱을 import 하지 않는 것은 기존 sweep 과 같다
# (앱 import 는 설정·환경변수를 요구해 오프라인 성질을 깬다). 값이 갈리면 --tau 계열로
# 덮어쓰고, 출력에 실제 사용값을 적어 어긋남이 드러나게 한다.
TAU_ABS = 0.30          # SEARCH_SIMILARITY_FLOOR
TAU_ABS_WORD = 0.24     # SEARCH_SIMILARITY_FLOOR_WORD
RATIO = 0.60            # SEARCH_TOP_RATIO
WORD_MAX_CHARS = 5      # SEARCH_WORD_QUERY_MAX_CHARS
SERVICE_LIMIT = 20      # 공용 계약 08 §6.1 의 size 기본값. word_sweep.py 와 같다

KS = (1, 3, 5)

# `-255` 가 원인을 가른 네 질의. P48 채택 조건이 개별 보고를 요구한다.
CASES = ("신한", "부캠", "그네", "스팟")


# --------------------------------------------------------------------------- 컷

def is_word_query(query: str, max_chars: int = WORD_MAX_CHARS) -> bool:
    """`app/service/search_service.py` 의 `_is_word_query` 와 같은 판정.

    공백은 `str.isspace()` 로 본다 — U+0020 만 보면 전각 공백으로 띄운 2어절이 「공백
    없음」으로 통과해 느슨한 하한을 탄다. 원본과 같은 이유로 두 조건을 함께 요구한다.
    """
    q = query.strip()
    return bool(q) and not any(c.isspace() for c in q) and len(q) <= max_chars


def cut(
    results: list[dict],
    query: str,
    *,
    tau: float = TAU_ABS,
    tau_word: float = TAU_ABS_WORD,
    ratio: float = RATIO,
    limit: int = SERVICE_LIMIT,
) -> list[dict]:
    """`_cut` 을 그대로 옮긴다. **비상 스위치가 단어형 분기보다 앞이다.**

    원본의 순서를 지킨다 — 뒤에 두면 `SEARCH_SIMILARITY_FLOOR=0` · `SEARCH_TOP_RATIO=0`
    을 넣어도 단어형만 계속 잘린다. 순서가 곧 동작이라 여기서도 같아야 한다.

    `limit` 은 컷보다 앞이다(서비스는 Query 의 `LIMIT` 뒤에 컷을 건다).
    """
    rows = results[:limit]
    if not rows:
        return rows
    if tau <= 0 and ratio <= 0:
        return rows
    floor = tau_word if is_word_query(query) else tau
    top = float(rows[0]["sim"])
    return [
        r for r in rows
        if float(r["sim"]) >= floor and float(r["sim"]) >= ratio * top
    ]


# ------------------------------------------------------------------------ 지표

def _relevant_ranks(rows: list[dict]) -> list[int]:
    """정답 행의 1-기반 위치. 입력 순서(유사도 내림차순)를 그대로 쓴다.

    행의 `rank` 필드가 아니라 **현재 목록에서의 위치**를 센다 — 컷 후에는 원래 `rank` 가
    구멍 난 상태라(2·5·9위만 남는 식) 그것으로 지표를 내면 사용자가 보는 목록과 어긋난다.
    """
    return [i for i, r in enumerate(rows, 1) if r.get("is_expected")]


def metrics_for(rows: list[dict], total_relevant: int) -> dict:
    """한 질의의 순위 지표. `total_relevant` 는 **컷 전 기준 전체 정답 수**다.

    분모를 컷 후로 잡으면 컷이 정답을 지울수록 Recall 이 올라가는 역전이 생긴다.
    """
    hits = _relevant_ranks(rows)
    out: dict = {"returned": len(rows)}

    out["mrr"] = 1.0 / hits[0] if hits else 0.0

    for k in KS:
        top = [h for h in hits if h <= k]
        out[f"hit@{k}"] = 1.0 if top else 0.0
        out[f"recall@{k}"] = (len(top) / total_relevant) if total_relevant else 0.0

        dcg = sum(1.0 / math.log2(h + 1) for h in top)
        ideal_n = min(total_relevant, k)
        idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_n + 1))
        out[f"ndcg@{k}"] = (dcg / idcg) if idcg else 0.0

    return out


def aggregate(per_query: list[dict]) -> dict:
    """질의 평균. 지표별 macro-average — 질의마다 정답 수가 달라 micro 는 긴 질의에 쏠린다."""
    if not per_query:
        return {}
    keys = [k for k in per_query[0] if k != "returned"]
    agg = {k: sum(q[k] for q in per_query) / len(per_query) for k in keys}
    agg["returned"] = sum(q["returned"] for q in per_query) / len(per_query)
    agg["n"] = len(per_query)
    return agg


def zero_rate(per_query: list[dict]) -> float:
    """0건 반환 비율. 무관 질의에서는 높을수록 좋고, 정답 질의에서는 낮을수록 좋다."""
    if not per_query:
        return 0.0
    return sum(1 for q in per_query if q["returned"] == 0) / len(per_query)


# ------------------------------------------------------------------- 적재·가드

class GuardError(Exception):
    """계산을 진행하면 안 되는 상태. 수치를 내지 않고 종료한다."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    if not path.exists():
        raise GuardError(f"행렬이 없다: {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check_rows(label: str, query: str, rows: list[dict]) -> None:
    for r in rows:
        if "sim" not in r or "rank" not in r:
            raise GuardError(f"{label} `{query}`: 행에 sim·rank 가 없다 — 순위를 셀 수 없다")


def _segment(
    label: str, entries: list[dict], *, answerable: bool
) -> list[tuple[str, list[dict], int]]:
    """(질의, 행, 전체 정답 수) 목록으로 정규화하며 라벨 정합을 검사한다."""
    out = []
    for e in entries:
        q = e["query"]
        rows = e.get("results") or []
        _check_rows(label, q, rows)
        n_rel = sum(1 for r in rows if r.get("is_expected"))
        if answerable and n_rel == 0:
            raise GuardError(
                f"{label} `{q}`: 정답 질의인데 정답 행이 0건이다 — 라벨이 어긋났다"
            )
        if not answerable and n_rel > 0:
            raise GuardError(
                f"{label} `{q}`: 무관 질의인데 정답 행이 있다 — 통제가 아니라 정답 있는 질의다"
            )
        out.append((q, rows, n_rel))
    return out


def _probe_segment(data: dict) -> list[tuple[str, list[dict], int]]:
    """`recall_probe.json` 은 `is_expected` 가 없다 — 상위 `expect`(장소명)로 표시한다."""
    out = []
    for e in data["queries"]:
        q, want = e["query"], e.get("expect")
        rows = [dict(r, is_expected=(r.get("name") == want)) for r in (e.get("results") or [])]
        _check_rows("probe", q, rows)
        out.append((q, rows, sum(1 for r in rows if r["is_expected"])))
    return out


# ------------------------------------------------------------------------ 실행

def score(seg: list[tuple[str, list[dict], int]], *, applied: bool, **cut_kw) -> dict:
    per = []
    for q, rows, n_rel in seg:
        shown = cut(rows, q, **cut_kw) if applied else rows[: cut_kw.get("limit", SERVICE_LIMIT)]
        per.append(metrics_for(shown, n_rel))
    agg = aggregate(per)
    agg["zero_rate"] = zero_rate(per)
    return agg


def case_rows(seg: list[tuple[str, list[dict], int]], **cut_kw) -> list[dict]:
    """사례 4건의 컷 전/후 순위. P48 채택 조건이 개별 보고를 요구한다."""
    out = []
    for q, rows, n_rel in seg:
        if q not in CASES:
            continue
        pre = _relevant_ranks(rows)
        post = _relevant_ranks(cut(rows, q, **cut_kw))
        out.append({
            "query": q,
            "relevant": n_rel,
            "rank_pre": pre[0] if pre else None,
            "rank_post": post[0] if post else None,
            "returned_post": len(cut(rows, q, **cut_kw)),
            "top1_sim": round(float(rows[0]["sim"]), 4) if rows else None,
        })
    return out


def _fmt(v) -> str:
    return "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def table(title: str, blocks: list[tuple[str, dict]]) -> None:
    print(f"\n{title}")
    cols = ["n", "hit@1", "hit@3", "hit@5", "mrr",
            "recall@1", "recall@3", "recall@5",
            "ndcg@1", "ndcg@3", "ndcg@5", "zero_rate", "returned"]
    print("  " + "세그먼트".ljust(22) + "".join(c.rjust(10) for c in cols))
    for name, m in blocks:
        if not m:
            print("  " + name.ljust(22) + "  (0건)")
            continue
        cells = "".join(
            (str(m[c]).rjust(10) if c == "n" else f"{m[c]:.4f}".rjust(10)) for c in cols
        )
        print("  " + name.ljust(22) + cells)


def main() -> int:
    ap = argparse.ArgumentParser(description="검색 순위 지표 baseline (P48 0단계)")
    ap.add_argument("--json", help="baseline JSON 저장 경로")
    ap.add_argument("--tau", type=float, default=TAU_ABS)
    ap.add_argument("--tau-word", type=float, default=TAU_ABS_WORD)
    ap.add_argument("--ratio", type=float, default=RATIO)
    ap.add_argument("--limit", type=int, default=SERVICE_LIMIT)
    args = ap.parse_args()

    paths = {
        "matrix": SEARCH / "matrix.json",
        "word_grid": SEARCH / "word_grid.json",
        "recall_probe": SEARCH / "recall_probe.json",
    }

    try:
        data = {k: _load(p) for k, p in paths.items()}

        profiles = {k: d.get("profile") for k, d in data.items()}
        if len(set(profiles.values())) != 1 or None in profiles.values():
            raise GuardError(f"Profile 이 어긋난다 — 한 표에 놓을 수 없다: {profiles}")
        profile = next(iter(profiles.values()))

        segs = {
            "문장형(정답)": _segment("matrix", data["matrix"]["queries"], answerable=True),
            "단어형(정답)": _segment("word", data["word_grid"]["queries"], answerable=True),
            "무관-문장형": _segment("matrix.offtopic", data["matrix"]["offtopic"], answerable=False),
            "무관-단어형": _segment("word.offtopic", data["word_grid"]["offtopic"], answerable=False),
            "타인소유-단어형": _segment("word.cross", data["word_grid"]["cross"], answerable=False),
            "진단프로브": _probe_segment(data["recall_probe"]),
        }
    except GuardError as exc:
        print(f"[가드] {exc}", file=sys.stderr)
        return 1

    cut_kw = dict(tau=args.tau, tau_word=args.tau_word, ratio=args.ratio, limit=args.limit)

    print("=" * 96)
    print("검색 순위 지표 baseline — P48 0단계")
    print("=" * 96)
    print(f"  Profile        {profile}")
    print(f"  Record         {data['word_grid']['record_count']}건 · 소유자 "
          f"{len(data['word_grid']['owners'])}명")
    print(f"  컷             tau_abs={args.tau} · tau_word={args.tau_word} · "
          f"r={args.ratio} · limit={args.limit}")
    print("  호출           DB 0회 · GMS 0회")

    result: dict = {
        "stage": "P48-0",
        "profile": profile,
        "cut": {"tau_abs": args.tau, "tau_abs_word": args.tau_word,
                "ratio": args.ratio, "limit": args.limit},
        "inputs": {k: {"path": str(p.relative_to(ROOT)), "sha256": _sha256(p)}
                   for k, p in paths.items()},
        "segments": {},
    }

    for applied, title in ((False, "컷 적용 전 (순위만)"), (True, "컷 적용 후 (사용자가 보는 것)")):
        blocks = []
        for name, seg in segs.items():
            m = score(seg, applied=applied, **cut_kw)
            blocks.append((name, m))
            result["segments"].setdefault(name, {})["cut_after" if applied else "cut_before"] = m
        table(title, blocks)

    cases = case_rows(segs["진단프로브"], **cut_kw)
    result["cases"] = cases
    print("\n사례별 (진단 프로브 · `-255` 가 원인을 가른 넷)")
    print("  " + "질의".ljust(10) + "정답수".rjust(8) + "컷전순위".rjust(10)
          + "컷후순위".rjust(10) + "컷후반환".rjust(10) + "top1_sim".rjust(12))
    for c in cases:
        print("  " + c["query"].ljust(10)
              + str(c["relevant"]).rjust(8)
              + _fmt(c["rank_pre"]).rjust(10)
              + _fmt(c["rank_post"]).rjust(10)
              + str(c["returned_post"]).rjust(10)
              + _fmt(c["top1_sim"]).rjust(12))

    print("\n읽는 법")
    print("  · 단어형과 문장형을 합산하지 않는다 — 두 대역이 겹치지 않는다(-266).")
    print("  · 무관/타인소유 세그먼트는 zero_rate 가 높을수록 좋다(침묵). 나머지 지표는 의미 없다.")
    print("  · 복수 정답이 있는 단어형은 MRR 만으로 보지 않는다 — recall@k · ndcg@k 를 함께 본다.")
    print("  · Record 수가 작아 절대값을 일반화하지 않는다. **퇴행 탐지 기준**으로만 쓴다.")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n저장: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

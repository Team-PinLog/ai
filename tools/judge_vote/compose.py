"""단일 판정 회차들을 **n회 다수결 회차로 접는다.** GMS 를 부르지 않는다.

    python tools/judge_vote/compose.py --n 1 3 5

## 왜 새로 부르지 않고 접는가

n회 다수결 1회분은 「같은 조건에서 독립으로 뽑은 판정 n개를 다수결한 것」이다. `-219`
하네스의 회차 하나도 정확히 같은 분포에서 독립으로 뽑은 판정이다. 그러므로 회차 3개를
묶어 다수결한 것과 n=3 을 실제로 한 번 돌린 것은 **같은 확률변수**다.

이 성질이 측정 비용을 결정적으로 바꾼다.

    따로 돌리면   n=1 10회 + n=3 10회 + n=5 6회 = 420 + 1,260 + 1,260 = 2,940 호출
    접으면        회차 30개 = 1,260 호출로 셋을 **전부** 얻는다

`-210` 이 유사도 행렬 하나로 임의의 τ 를 재구성한 것과 같은 수법이다. 저쪽은 τ 가
임베딩을 안 바꿔서 됐고, 이쪽은 회차가 서로 독립이라 된다.

**다수결 규칙은 서비스 코드를 그대로 부른다**(`app.service.judge_vote.combine`). 여기에
같은 식을 다시 적으면 「우리가 잰 규칙」과 「서버가 쓰는 규칙」이 갈라진다 —
`tau_grid/matrix.py` 가 `_topk` 를 그대로 부르는 것과 같은 이유다.

## 한계 — 숨기지 않는다

접은 조건들이 **같은 호출 풀에서 나온다.** n=1 관측과 n=3 관측이 독립 표본이 아니라
같은 회차를 공유하므로, 두 조건을 완전히 따로 뽑았을 때보다 서로 닮는다. 그래서

  * 평균 비교와 범위 비교는 그대로 유효하다 — 짝지어진 설계는 오히려 잡음이 준다
  * `score_ab.py` 의 순열검정은 표본 독립을 가정하므로 **참고값으로만** 읽는다
  * 분할 방식이 결과를 만들지 않았는지 `--shuffle` 재분할로 확인한다

그리고 접은 값이 실제 n회 호출과 같은지는 접어서 확인할 수 없다. `run_live.py` 가
실제 n=3 경로를 돌려 대조한다.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.schema.llm import JudgeResult, KeywordSelection  # noqa: E402
from app.service import judge_vote  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


def load_reps(d: Path, variant: str) -> list[dict]:
    recs = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(d.glob(f"{variant}-r*.json"))
    ]
    recs.sort(key=lambda r: r["rep"])
    return recs


def code_ids(matrix: Path) -> dict[str, int]:
    """`code → keyword_id`. 회차 파일은 code 로 적혀 있고 다수결 규칙은 id 로 돈다."""
    data = json.loads(matrix.read_text(encoding="utf-8"))
    return {x["code"]: x["id"] for c in data["contexts"] for x in c["candidates"]}


def as_result(rec: dict, cid: str, ids: dict[str, int]) -> JudgeResult:
    confs = rec.get("confidences", {}).get(cid, {})
    return JudgeResult(
        selected=[
            KeywordSelection(keyword_id=ids[code], confidence=confs.get(code))
            for code in rec["selections"][cid]
        ],
        model=next(iter(rec.get("models", {})), None),
    )


def compose(group: list[dict], n: int, ids: dict[str, int], codes: dict[int, str]) -> dict:
    """회차 n개를 다수결 회차 하나로 접는다.

    **실패한 회차의 처리가 여기서 규칙이 된다.** `run.py` 는 호출이 실패한 Context 를
    `selections` 에 아예 넣지 않는다(값이 없는 것과 "빈 선택"이 구분된다). 그것을 빈
    선택으로 읽으면 실패가 「아무것도 안 붙었다」라는 **판정 결과로 둔갑**하고, 다수결에서는
    그 표가 반대표로 세어져 개선처럼 보인다. 서비스와 같은 정족수 규칙을 그대로 댄다.
    """
    all_cids = sorted({c for r in group for c in r["selections"]}, key=int)
    selections: dict[str, list[str]] = {}
    confidences: dict[str, dict[str, float | None]] = {}
    failures: list[dict] = []

    for cid in all_cids:
        present = [r for r in group if cid in r["selections"]]
        if not judge_vote.has_quorum(len(present), n):
            failures.append({"context_id": int(cid), "error": f"quorum not met ({len(present)}/{n})"})
            continue
        out = judge_vote.combine([as_result(r, cid, ids) for r in present], n)
        selections[cid] = sorted(codes[s.keyword_id] for s in out.selected)
        confidences[cid] = {codes[s.keyword_id]: s.confidence for s in out.selected}

    calls = sum(r["llm_calls"] for r in group)
    per_call = [r["elapsed_sec"] / r["llm_calls"] for r in group if r["llm_calls"]]
    return {
        "variant": f"n{n}",
        "selections": selections,
        "confidences": confidences,
        "failures": failures,
        "models": {m: sum(r["models"].get(m, 0) for r in group) for m in
                   {m for r in group for m in r["models"]}},
        "llm_calls": calls,
        # 순차로 돌렸다면 회차들의 합, 동시에 돌렸다면 가장 느린 회차. 실제 값은
        # `run_live.py` 가 잰다 — 여기 둘은 상한과 하한이다.
        "elapsed_sec": round(sum(r["elapsed_sec"] for r in group), 1),
        "elapsed_parallel_sec": round(max(r["elapsed_sec"] for r in group), 1),
        "sec_per_call": round(st.mean(per_call), 3) if per_call else None,
        "source_reps": [r["rep"] for r in group],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, nargs="+", default=[1, 3, 5])
    ap.add_argument("--variant", default="A")
    ap.add_argument("--runs", default=str(ROOT / ".prompt_ab" / "runs"))
    ap.add_argument("--outdir", default=str(ROOT / ".judge_vote" / "runs"))
    ap.add_argument("--matrix", default=str(ROOT / ".tau" / "matrix.json"))
    ap.add_argument("--cap", type=int, default=0, help="조건당 관측 수 상한(0=제한 없음)")
    ap.add_argument(
        "--shuffle",
        type=int,
        default=0,
        help="분할 전에 회차 순서를 섞는다(시드). 분할 방식이 결과를 만들지 않았는지 본다",
    )
    args = ap.parse_args()

    reps = load_reps(Path(args.runs), args.variant)
    if not reps:
        raise SystemExit(f"회차 파일이 없다: {args.runs}/{args.variant}-r*.json")
    ids = code_ids(Path(args.matrix))
    codes = {v: k for k, v in ids.items()}

    order = list(reps)
    if args.shuffle:
        random.Random(args.shuffle).shuffle(order)
    log(f"  회차 {len(reps)}개 (r{reps[0]['rep']}~r{reps[-1]['rep']}) · "
        f"프리셋 코드 {len(ids)}개 · 분할 {'셔플 seed=' + str(args.shuffle) if args.shuffle else '순서대로'}")

    fails = sum(len(r["failures"]) for r in reps)
    if fails:
        log(f"  [주의] 원본 회차에 실패한 호출 {fails}건 — 정족수 규칙이 걸린다")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob("*.json"):
        f.unlink()  # 분할이 바뀌면 이전 합성 회차가 섞이면 안 된다

    log()
    log(f"  {'조건':<6} {'관측':>4} {'회차/관측':>8} {'호출/관측':>9} "
        f"{'순차 s':>8} {'동시 s':>7}")
    log("  " + "─" * 52)
    summary = []
    for n in args.n:
        groups = [order[i:i + n] for i in range(0, len(order) - n + 1, n)]
        if args.cap:
            groups = groups[: args.cap]
        if not groups:
            log(f"  n={n}: 회차가 모자라 만들 수 없다")
            continue
        recs = []
        for k, g in enumerate(groups, 1):
            rec = compose(g, n, ids, codes)
            rec["rep"] = k
            (outdir / f"n{n}-r{k}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            recs.append(rec)
        summary.append({
            "n": n,
            "observations": len(recs),
            "calls_per_obs": st.mean(r["llm_calls"] for r in recs),
            "sequential_sec": st.mean(r["elapsed_sec"] for r in recs),
            "parallel_sec": st.mean(r["elapsed_parallel_sec"] for r in recs),
            "composed_failures": sum(len(r["failures"]) for r in recs),
        })
        log(f"  n={n:<4} {len(recs):>4} {n:>8} {summary[-1]['calls_per_obs']:>9.0f} "
            f"{summary[-1]['sequential_sec']:>8.1f} {summary[-1]['parallel_sec']:>7.1f}")

    log()
    log(f"  → {outdir}")
    log(f"  이어서: score_ab.py --runs {outdir} --base n1 --cond n3")
    (Path(args.outdir).parent / "cost.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""`T69` 가 세지 않는 행을 **재현 가능하게** 센다. `S15P11A705-228`. 파일만 읽는다.

## 왜 이 파일이 따로 있는가

`tools/tau_grid/score.py` 의 격자 루프가 `rank <= k` 를 먼저 걸어서, **rank 로 밀려난
행은 어느 칸에도 세어지지 않는다**(T69). τ 만 조건일 때는 rank 가 고정이라 무해했지만
이 티켓은 임베딩을 바꾸므로 rank 가 변한다.

    tau-score-*.json  τ=0.30 행이 `lost_fit: []` · `removed.fit: 0` 이라고 낸다
    실제              `kept.fit` 이 base 63 → D 60 으로 줄어 있다. 3행이 증발했다

`score.py` 를 고치지 않은 것은 그 파일이 `-210` 의 수치를 재현하는 기준점이기 때문이다
(T69 §처방). 대신 **밀려난 행을 여기서 따로 세어 리포트에 나란히 낸다.**

## 두 손실을 왜 갈라 세나

T69 가 다음 사람에게 남긴 지시 그대로다 — 같은 칸에 합치면 「τ 를 올려서 잃었다」와
「다른 프리셋이 밀어내서 잃었다」가 구분되지 않는다. 전자는 τ 를 내리면 돌아오고
후자는 돌아오지 않는다. **개정의 대가는 후자다.**

    tau_cut     rank <= k 인데 sim < τ         τ 를 내리면 돌아온다
    rank_push   sim >= τ 인데 rank > k         다른 프리셋이 밀어냈다 — τ 로 못 돌린다
    both        rank > k 이고 sim < τ
    absent      행렬의 후보 목록에 아예 없다

    python tools/preset_desc/rank_loss.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "prompt_ab"))

from score_ab import load_labels  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

VERDICTS = ("fit", "unfit", "unclear")
REASONS = ("tau_cut", "rank_push", "both", "absent")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def reachable_map(matrix: dict, k: int, tau: float) -> dict[tuple[int, str], dict]:
    """`(context, code)` → 그 쌍의 sim·rank. 후보 여부는 호출자가 판단한다."""
    out: dict[tuple[int, str], dict] = {}
    for c in matrix["contexts"]:
        cid = int(c["context_id"])
        for x in c["candidates"]:
            out[(cid, x["code"])] = {"sim": x["sim"], "rank": x["rank"]}
    return out


def classify(info: dict | None, k: int, tau: float) -> str | None:
    """후보에서 빠졌으면 그 사유를, 후보면 `None` 을 돌려준다."""
    if info is None:
        return "absent"
    ok_rank = info["rank"] <= k
    ok_tau = info["sim"] >= tau
    if ok_rank and ok_tau:
        return None
    if ok_rank and not ok_tau:
        return "tau_cut"
    if not ok_rank and ok_tau:
        return "rank_push"
    return "both"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conds", nargs="+", default=["base", "base2", "D", "E", "DE"])
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--tau", type=float, default=0.30)
    ap.add_argument("--labels", default=str(ROOT / "tools" / "tau_grid" / "labels.yaml"))
    ap.add_argument(
        "--extra", default=str(ROOT / "tools" / "prompt_ab" / "labels_extra.yaml")
    )
    ap.add_argument("--matrix-dir", default=str(ROOT / ".preset_desc"))
    ap.add_argument("--out", default=str(ROOT / ".preset_desc" / "rank-loss.json"))
    args = ap.parse_args()

    # `labels.yaml` 만 쓴다. `labels_extra.yaml` 은 **재판정에서 새로 나타난 조합**이라
    # 현행 판정 83행의 모집단이 아니다. 섞으면 조건마다 모집단이 달라진다.
    labels = load_labels(Path(args.labels))
    log(f"  현행 판정 라벨 {len(labels)}행 · k={args.k} · τ={args.tau}")

    mdir = Path(args.matrix_dir)
    result: dict[str, dict] = {}

    log("")
    log(f"  {'조건':<8}{'빠진 행':>8}{'fit':>6}{'unfit':>7}{'unclear':>9}"
        f"   {'tau_cut':>8}{'rank_push':>10}{'both':>6}{'absent':>8}")
    log("  " + "-" * 76)

    for cond in args.conds:
        path = mdir / f"matrix-{cond}.json"
        if not path.exists():
            log(f"  {cond:<8} 행렬 없음 ({path.name}) — 건너뛴다")
            continue
        matrix = json.loads(path.read_text(encoding="utf-8"))
        pairs = reachable_map(matrix, args.k, args.tau)

        by_verdict = {v: 0 for v in VERDICTS}
        by_reason = {r: 0 for r in REASONS}
        rows = []
        for key, verdict in labels.items():
            why = classify(pairs.get(key), args.k, args.tau)
            if why is None:
                continue
            by_verdict[verdict] += 1
            by_reason[why] += 1
            info = pairs.get(key) or {}
            rows.append({
                "context": key[0], "code": key[1], "verdict": verdict, "reason": why,
                "sim": info.get("sim"), "rank": info.get("rank"),
            })

        total = sum(by_verdict.values())
        log(f"  {cond:<8}{total:>8}{by_verdict['fit']:>6}{by_verdict['unfit']:>7}"
            f"{by_verdict['unclear']:>9}   {by_reason['tau_cut']:>8}"
            f"{by_reason['rank_push']:>10}{by_reason['both']:>6}{by_reason['absent']:>8}")
        result[cond] = {
            "dropped_total": total, "by_verdict": by_verdict,
            "by_reason": by_reason, "rows": sorted(rows, key=lambda r: (r["context"], r["code"])),
        }

    log("")
    log("  fit 이 빠지는 것이 사용자 손실이고, unfit 이 빠지는 것이 이 개정이 노린 것이다.")
    log("  `rank_push` 는 τ 를 내려도 돌아오지 않는다 — 다른 프리셋이 자리를 가져갔다.")

    if "base" in result and result["base"]["dropped_total"] != 0:
        log("")
        log("  ** base 에서 빠진 행이 0 이 아니다. 라벨의 모집단이 현행 후보 집합과")
        log("     어긋났다는 뜻이므로 조건 비교 전에 그것부터 봐야 한다. **")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"k": args.k, "tau": args.tau, "label_rows": len(labels),
                    "conditions": result}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

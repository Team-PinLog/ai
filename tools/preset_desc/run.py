"""조건 하나를 N회 판정한다. `S15P11A705-228`. **GMS 판정을 부르는 유일한 파일.**

`prompt_ab/run.py` 를 본떴고 출력 스키마도 같게 두었다 — `prompt_ab/score_ab.py` 를
그대로 재사용하기 위해서다. **집계기를 새로 짜면 `-219`·`-223` 과 같은 자를 대고 있다는
보장이 사라진다.**

다른 점 둘.

    조건이 프롬프트가 아니라 프리셋이다   `llm_client.SYSTEM` 은 건드리지 않는다
    후보 집합이 조건마다 다르다           description 이 임베딩에 들어가므로 당연하다

두 번째가 `-219` 와 결정적으로 다른 지점이다. 저쪽은 후보 집합을 조건 사이에 **완전히
같게** 두는 것이 설계의 핵심이었다(조건은 프롬프트뿐이어야 하므로). 여기서는 후보가
달라지는 것 자체가 조건의 효과 일부다 — `description` 은 후보 선정 층과 판정 층 **양쪽**에
들어가고, 그 둘을 동시에 움직이는 것이 이 티켓이 여기를 고른 이유다.

    python tools/preset_desc/run.py --cond base --reps 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.client import llm_client as llm_mod  # noqa: E402
from app.client.llm_client import LLMClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from variants import CONDITIONS, build  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


async def one_rep(
    client: LLMClient, data: dict, presets: dict, k: int, tau: float
) -> dict:
    """한 회차. Context 를 순서대로 판정한다.

    **한 건이 실패해도 회차를 버리지 않는다**(`prompt_ab/run.py` 와 같은 이유). 결측은
    `failures` 로 남고 `score_ab.py` 가 경고한다.
    """
    selections: dict[str, list[str]] = {}
    confidences: dict[str, dict[str, float | None]] = {}
    failures: list[dict] = []
    models: dict[str, int] = {}
    calls = 0
    t0 = time.monotonic()

    for c in data["contexts"]:
        cid = c["context_id"]
        cands = [x for x in c["candidates"] if x["rank"] <= k and x["sim"] >= tau]
        if not cands:
            selections[str(cid)] = []
            continue
        calls += 1
        try:
            result = await client.judge(c["body"], [presets[x["id"]] for x in cands])
        except Exception as exc:  # noqa: BLE001 — 무엇이든 회차를 죽이지 않는다
            failures.append({"context_id": cid, "error": f"{type(exc).__name__}: {exc}"})
            log(f"    [FAIL] context {cid}: {type(exc).__name__}: {exc}")
            continue
        models[result.model] = models.get(result.model, 0) + 1
        allowed = {x["id"] for x in cands}
        # `KeywordService._map` 과 같은 규칙 — 후보 밖 선택은 버린다.
        kept = [s for s in result.selected if s.keyword_id in allowed]
        selections[str(cid)] = sorted(presets[s.keyword_id]["code"] for s in kept)
        confidences[str(cid)] = {
            presets[s.keyword_id]["code"]: s.confidence for s in kept
        }

    return {
        "selections": selections,
        "confidences": confidences,
        "failures": failures,
        "models": models,
        "llm_calls": calls,
        "elapsed_sec": round(time.monotonic() - t0, 1),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cond", required=True, choices=sorted(CONDITIONS))
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--start-rep", type=int, default=1)
    ap.add_argument("--matrixdir", default=str(ROOT / ".preset_desc"))
    ap.add_argument("--outdir", default=str(ROOT / ".preset_desc" / "runs"))
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--tau", type=float, default=0.30)
    ap.add_argument(
        "--chain",
        default="openai:gpt-4o-mini",
        help="판정 벤더를 하나로 고정한다(-219·-223 과 같은 값). 빈 문자열이면 설정 체인",
    )
    args = ap.parse_args()

    matrix = Path(args.matrixdir) / f"matrix-{args.cond}.json"
    if not matrix.exists():
        raise SystemExit(f"{matrix} 가 없다. matrix.py 를 먼저 돌려라.")
    data = json.loads(matrix.read_text(encoding="utf-8"))
    if data.get("condition") != args.cond:
        # 조건과 행렬이 어긋나면 「조건 D 인데 base 후보로 판정」 같은 상태가 조용히
        # 만들어진다. 그 회차는 어느 조건의 값도 아니게 된다.
        raise SystemExit(
            f"행렬의 조건이 다르다: {data.get('condition')} != {args.cond}"
        )

    # **판정 프롬프트는 현행 그대로다.** 이 티켓은 `description` 만 바꿔야 원인을 가를
    # 수 있다(계약). 어긋나 있으면 그것을 모르고 지나가지 않게 찍는다.
    settings = get_settings()
    presets = {
        p["id"]: {
            "id": p["id"],
            "display_name": p["display_name"],
            "category": p["category"],
            "description": p["description"],
            "examples": p["examples"],
            "code": p["code"],
        }
        for p in build(args.cond)
    }
    log(f"  조건 {args.cond} · 프리셋 {len(presets)}개 · 프롬프트 현행({len(llm_mod.SYSTEM)}자)")

    chain = (
        tuple(tuple(x.split(":", 1)) for x in args.chain.split(","))
        if args.chain
        else settings.judge_vendors
    )
    log(f"  체인 {chain} · τ={args.tau} k={args.k} · 행렬 {matrix.name}")
    log(
        f"  Context {len(data['contexts'])}건 · 회차 "
        f"{args.start_rep}~{args.start_rep + args.reps - 1}"
    )

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    for rep in range(args.start_rep, args.start_rep + args.reps):
        out = outdir / f"{args.cond}-r{rep}.json"
        if out.exists():
            log(f"  r{rep} 이미 있음 — 건너뛴다 ({out.name})")
            continue
        log(f"  r{rep} 시작 …")
        # 클라이언트를 회차마다 새로 만든다. 시도 카운터가 회차 사이로 새지 않게.
        client = LLMClient(
            gms_base_url=settings.gms_base_url, api_key=settings.gms_api_key, chain=chain
        )
        rec = await one_rep(client, data, presets, args.k, args.tau)
        rec.update(
            {
                # `score_ab.py` 가 `variant` 로 조건을 묶는다. 이름을 맞춰 둔다.
                "variant": args.cond,
                "condition": args.cond,
                "rep": rep,
                "chain": [list(x) for x in chain],
                "tau": args.tau,
                "k": args.k,
                "matrix": str(matrix),
                "presets": {p["code"]: {
                    "description": p["description"], "examples": p["examples"]
                } for p in build(args.cond)},
            }
        )
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        rows = sum(len(v) for v in rec["selections"].values())
        log(
            f"  r{rep} 완료 — 선택 {rows}행 · LLM {rec['llm_calls']}회 · "
            f"실패 {len(rec['failures'])} · {rec['elapsed_sec']}s · 모델 {rec['models']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

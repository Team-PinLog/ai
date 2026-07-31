"""오프라인 재구성이 실제 재판정과 얼마나 어긋나는지 잰다.

`sweep.py`·`score.py` 의 모든 숫자는 **한 가정** 위에 있다 — τ 를 올려 후보가 줄어도
살아남은 후보에 대한 판정은 그대로라는 것. 실제로는 후보 목록이 프롬프트의 일부라
줄어들면 판정이 흔들릴 수 있다. 그 흔들림의 크기를 모르면 재구성을 근거로 쓸 수 없다.

    python tools/tau_grid/verify_reconstruction.py --tau 0.34

**DB 를 건드리지 않는다.** 재시딩도 서버 기동도 없이 `LLMClient.judge` 를 직접 부르고
결과를 비교만 한다 — 저장 경로는 이 티켓이 바꾸는 곳이 아니므로 잴 필요가 없고,
재시딩은 임베딩까지 다시 만들어 GMS 비용을 몇 배로 키운다.

판정은 비결정적이다. 그래서 **τ 를 바꾼 실행과 바꾸지 않은 실행을 둘 다 돌린다** —
차이가 τ 때문인지 그냥 흔들림인지는 대조군 없이 가를 수 없다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.cache.preset_cache import PresetCache  # noqa: E402
from app.client.llm_client import LLMClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repository import keyword_preset_repo  # noqa: E402

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


async def judge_all(client: LLMClient, data: dict, presets: dict, k: int, tau: float):
    """τ 로 후보를 잘라 실제 판정을 돌린다. 후보 0개면 부르지 않는다(현행 경로 그대로)."""
    out: dict[int, set[str]] = {}
    calls = 0
    for c in data["contexts"]:
        cands = [x for x in c["candidates"] if x["rank"] <= k and x["sim"] >= tau]
        if not cands:
            out[c["context_id"]] = set()
            continue
        calls += 1
        result = await client.judge(c["body"], [presets[x["id"]] for x in cands])
        allowed = {x["id"] for x in cands}
        # `KeywordService._map` 과 같은 규칙 — 후보 밖 선택은 버린다.
        out[c["context_id"]] = {
            presets[s.keyword_id]["code"]
            for s in result.selected
            if s.keyword_id in allowed
        }
    return out, calls


def compare(name: str, baseline: dict, actual: dict) -> dict:
    """두 판정 집합의 차이. Context 단위로 센다."""
    added = removed = 0
    diff_ctx = 0
    for cid, want in baseline.items():
        got = actual.get(cid, set())
        a, r = len(got - want), len(want - got)
        added += a
        removed += r
        if a or r:
            diff_ctx += 1
    total = sum(len(v) for v in baseline.values())
    log(
        f"  {name:<28} 예측 {total:>3}행 · 어긋난 Context {diff_ctx:>2}/{len(baseline)} "
        f"· 예측에 없던 선택 +{added} · 예측이 있다던 선택 -{removed}"
    )
    return {"predicted_rows": total, "diff_contexts": diff_ctx, "added": added, "removed": removed}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=str(ROOT / ".tau" / "matrix.json"))
    ap.add_argument("--tau", type=float, default=0.34)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / ".tau" / "verify.json"))
    args = ap.parse_args()

    data = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    settings = get_settings()

    db = Database(settings.database_url)
    await db.connect()
    cache = PresetCache()
    try:
        async with db.acquire() as conn:
            cache.load(await keyword_preset_repo.load_active(conn, settings.embedding_profile))
    finally:
        await db.disconnect()
    presets = {
        p.id: {
            "id": p.id,
            "display_name": p.display_name,
            "category": p.category,
            "description": p.description,
            "examples": p.examples,
            "code": p.code,
        }
        for p in cache.snapshot().presets
    }

    client = LLMClient(
        gms_base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        chain=settings.judge_vendors,
    )

    # DB 에 저장된 현행 판정. 재구성의 출발점이다.
    stored = {
        c["context_id"]: {x["code"] for x in c["candidates"] if x["selected"]}
        for c in data["contexts"]
    }
    # 오프라인 재구성 — τ 미만인 선택만 사라진다는 가정.
    predicted = {
        c["context_id"]: {
            x["code"]
            for x in c["candidates"]
            if x["selected"] and x["rank"] <= args.k and x["sim"] >= args.tau
        }
        for c in data["contexts"]
    }

    head(f"대조군 — τ=0.30(현행) 을 그대로 다시 판정한다")
    log("  차이가 τ 때문인지 판정의 비결정성인지 가르기 위한 것이다.")
    control, c_calls = await judge_all(client, data, presets, args.k, 0.30)
    ctrl = compare("τ=0.30 재판정 vs 저장값", stored, control)
    log(f"  LLM 호출 {c_calls}회")

    head(f"본실험 — τ={args.tau} 로 후보를 잘라 판정한다")
    actual, a_calls = await judge_all(client, data, presets, args.k, args.tau)
    exp = compare(f"τ={args.tau} 재판정 vs 재구성 예측", predicted, actual)
    log(f"  LLM 호출 {a_calls}회")

    head("판독")
    log(f"  비결정성만으로 어긋나는 Context   {ctrl['diff_contexts']}/{len(stored)}")
    log(f"  τ 를 바꿨을 때 어긋나는 Context   {exp['diff_contexts']}/{len(predicted)}")
    if exp["diff_contexts"] <= ctrl["diff_contexts"]:
        log("  → τ 변경이 만드는 추가 어긋남이 비결정성 이하다. 재구성을 근거로 쓸 수 있다")
    else:
        log(
            f"  → τ 변경이 비결정성보다 {exp['diff_contexts'] - ctrl['diff_contexts']}건 "
            "더 어긋난다. 재구성 수치는 그만큼의 오차를 안고 읽어야 한다"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "tau": args.tau,
                "k": args.k,
                "control": ctrl,
                "experiment": exp,
                "llm_calls": c_calls + a_calls,
                "control_result": {str(k): sorted(v) for k, v in control.items()},
                "actual_result": {str(k): sorted(v) for k, v in actual.items()},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"\n  → {out}  (LLM 총 {c_calls + a_calls}회)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

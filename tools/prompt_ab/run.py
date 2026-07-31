"""판정 프롬프트 A/B 를 **반복해서** 돌린다. 회차 하나가 끝날 때마다 파일로 남긴다.

    python tools/prompt_ab/run.py --variant A --reps 5
    python tools/prompt_ab/run.py --variant B --reps 5

`tau_grid/verify_reconstruction.py` 의 `judge_all` 을 본떴다. 다른 점 셋.

  조건이 τ 가 아니라 프롬프트다   `llm_client.SYSTEM` 을 갈아 끼운다
  회차를 반복한다                 1회 비교로는 개선인지 흔들림인지 갈리지 않는다(T39)
  회차마다 즉시 쓴다              칩 세션은 예고 없이 죽고 GMS 호출은 되돌릴 수 없다

**DB 는 후보·본문을 읽을 때만 쓰고 쓰지 않는다.** `.tau/matrix.json` 이 유사도와 본문을
이미 들고 있으므로 재임베딩도 재시딩도 없다 — 조건은 프롬프트뿐이고 후보 집합은
A·B 가 완전히 같아야 한다.

## 벤더를 하나로 묶는 이유

폴백 체인이 살아 있으면 429 한 번에 다음 벤더로 넘어가고, 그 회차만 다른 모델이
판정한다. `-175` 에서 같은 프롬프트로도 벤더별 일치도가 0.53~0.93 으로 갈렸으므로
그 혼입은 프롬프트 효과와 구분되지 않는다. `--chain` 으로 단일 벤더를 강제하고,
실제로 답한 모델(`JudgeResult.model`)을 회차 파일에 남겨 사후에 확인할 수 있게 둔다.
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

from app.cache.preset_cache import PresetCache  # noqa: E402
from app.client import llm_client as llm_mod  # noqa: E402
from app.client.llm_client import LLMClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repository import keyword_preset_repo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from variants import VARIANTS  # noqa: E402

# T28·T38 — 콘솔이 cp949 면 `—` 한 글자에 죽는다. 호출자가 PYTHONIOENCODING 을
# 기억하지 않아도 되게 스크립트가 스스로 막는다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


async def load_presets(settings) -> dict:
    db = Database(settings.database_url)
    await db.connect()
    cache = PresetCache()
    try:
        async with db.acquire() as conn:
            cache.load(
                await keyword_preset_repo.load_active(conn, settings.embedding_profile)
            )
    finally:
        await db.disconnect()
    return {
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


async def one_rep(client: LLMClient, data: dict, presets: dict, k: int, tau: float) -> dict:
    """한 회차. 42 Context 를 순서대로 판정한다.

    **한 건이 실패해도 회차를 버리지 않는다.** 42회 중 1건 때문에 나머지 41건의 GMS
    호출을 버리는 것이 더 큰 손실이고, 결측은 집계에서 셀 수 있게 남겨 두면 된다.
    다만 결측이 있는 회차는 `score_ab.py` 가 경고한다 — 조건 간 호출 수가 달라지면
    비교가 그만큼 기운다.
    """
    selections: dict[str, list[str]] = {}
    # `confidence` 를 따로 남긴다. 이 티켓은 쓰지 않지만 **다시 부르지 않으면 얻을 수
    # 없는 값**이고, 현행 판정 1회분에서 이미 유사도보다 훨씬 잘 가르는 신호가 보였다
    # (구현 리포트 §6). 후속이 재측정 없이 그 신호가 회차에서 재현되는지 볼 수 있게 둔다.
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
    ap.add_argument("--variant", required=True, choices=sorted(VARIANTS))
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--start-rep", type=int, default=1)
    ap.add_argument("--matrix", default=str(ROOT / ".tau" / "matrix.json"))
    ap.add_argument("--outdir", default=str(ROOT / ".prompt_ab" / "runs"))
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--tau", type=float, default=0.30)
    ap.add_argument(
        "--chain",
        default="openai:gpt-4o-mini",
        help="판정 벤더를 하나로 고정한다. 빈 문자열이면 설정의 폴백 체인을 그대로 쓴다",
    )
    args = ap.parse_args()

    system = VARIANTS[args.variant]

    # A 가 현행 코드와 어긋나 있지 않은지 본다. 채택 전에는 같아야 하고, B 를 채택한
    # 뒤에는 어긋나는 것이 정상이다 — 어느 쪽이든 **모르고 지나가지 않게** 찍는다.
    if args.variant == "A" and system != llm_mod.SYSTEM:
        log("  [주의] variants.A 가 현행 llm_client.SYSTEM 과 다르다.")
        log("         B 를 이미 채택했다면 정상이다. 아니라면 A 정본을 맞춰야 한다.")

    data = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    settings = get_settings()
    presets = await load_presets(settings)
    # T33 — `.env` 의 `DATABASE_URL` 은 07-27 잔재로 `:5433` 을 가리키고 그쪽에는
    # 프리셋이 없다. 빈 캐시로 진행하면 후보 조회가 KeyError 로 죽거나 더 나쁘게는
    # 「선택 0행」을 결과로 낸다. 재기 전에 멈춘다.
    if not presets:
        raise SystemExit(
            f"프리셋이 0개다. DATABASE_URL 이 시연 DB(:15432)를 가리키는지 보라 (T33). "
            f"현재: {settings.database_url.rsplit('@', 1)[-1]}"
        )
    log(f"  프리셋 {len(presets)}개 · DB {settings.database_url.rsplit('@', 1)[-1]}")

    chain = (
        tuple(tuple(x.split(":", 1)) for x in args.chain.split(","))
        if args.chain
        else settings.judge_vendors
    )
    log(f"  조건 {args.variant} · 체인 {chain} · τ={args.tau} k={args.k}")
    log(f"  Context {len(data['contexts'])}건 · 회차 {args.start_rep}~{args.start_rep + args.reps - 1}")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    for rep in range(args.start_rep, args.start_rep + args.reps):
        out = outdir / f"{args.variant}-r{rep}.json"
        if out.exists():
            # 재개. 이미 쓴 회차를 다시 부르는 것은 GMS 를 이유 없이 쓰는 것이다.
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
                "variant": args.variant,
                "rep": rep,
                "chain": [list(x) for x in chain],
                "tau": args.tau,
                "k": args.k,
                "system_prompt": system,
                "matrix": str(args.matrix),
            }
        )
        out.write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rows = sum(len(v) for v in rec["selections"].values())
        log(
            f"  r{rep} 완료 — 선택 {rows}행 · LLM {rec['llm_calls']}회 · "
            f"실패 {len(rec['failures'])} · {rec['elapsed_sec']}s · 모델 {rec['models']}"
        )
        log(f"       → {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""**실제 서비스 다수결 경로**를 n회 호출로 돌린다. `compose.py` 의 대조군이다.

    python tools/judge_vote/run_live.py --n 3 --reps 3

`compose.py` 는 회차를 접어 n회 다수결을 재구성한다. 그 재구성이 맞다는 근거는 「회차가
서로 독립」이라는 가정 하나뿐이고, **접어서는 그 가정을 확인할 수 없다.** 이 파일이
`KeywordService._judge_n` 을 그대로 불러 실제로 n회 호출한 결과를 낸다.

셋을 확인한다.

    분포        접은 n=3 과 실제 n=3 의 오분류·fit 이 같은 범위에 있는가
    지연        동시 호출이 실제로 1회분 지연에 수렴하는가 (`compose` 는 추정만 한다)
    호출 수     Context 1건당 정확히 n회인가 (재시도가 섞이면 어긋난다)

`KeywordService` 를 db 없이 만든다 — `_judge_n` 은 `self._llm` 과 `self._settings` 만
쓰므로 DB 결선 없이 그 메서드만 실행된다. **판정 규칙을 여기 다시 적지 않는 것이
핵심이다**; 다시 적으면 대조군이 대조 대상과 같은 실수를 공유한다.

출력 형식은 `run.py` 와 같아서 `score_ab.py` 가 그대로 읽는다.
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
from app.client.llm_client import LLMClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repository import keyword_preset_repo  # noqa: E402
from app.service.keyword_service import KeywordService  # noqa: E402

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
            "id": p.id, "display_name": p.display_name, "category": p.category,
            "description": p.description, "examples": p.examples, "code": p.code,
        }
        for p in cache.snapshot().presets
    }


async def one_rep(svc: KeywordService, client, data: dict, presets: dict,
                  k: int, tau: float, n: int) -> dict:
    selections: dict[str, list[str]] = {}
    confidences: dict[str, dict[str, float | None]] = {}
    failures: list[dict] = []
    latencies: list[float] = []
    calls = 0
    t0 = time.monotonic()

    for c in data["contexts"]:
        cid = c["context_id"]
        cands = [x for x in c["candidates"] if x["rank"] <= k and x["sim"] >= tau]
        if not cands:
            selections[str(cid)] = []
            continue
        before = client.call_count
        t1 = time.monotonic()
        try:
            # 실제 서비스 메서드. 동시 호출·정족수·다수결이 전부 이 안에 있다.
            result = await svc._judge_n(c["body"], [presets[x["id"]] for x in cands], cid)
        except Exception as exc:  # noqa: BLE001 — 회차를 죽이지 않는다(run.py 와 같다)
            failures.append({"context_id": cid, "error": f"{type(exc).__name__}: {exc}"})
            log(f"    [FAIL] context {cid}: {type(exc).__name__}: {exc}")
            calls += client.call_count - before
            continue
        latencies.append(time.monotonic() - t1)
        calls += client.call_count - before
        allowed = {x["id"] for x in cands}
        kept = [s for s in result.selected if s.keyword_id in allowed]
        selections[str(cid)] = sorted(presets[s.keyword_id]["code"] for s in kept)
        confidences[str(cid)] = {presets[s.keyword_id]["code"]: s.confidence for s in kept}

    return {
        "selections": selections,
        "confidences": confidences,
        "failures": failures,
        "models": dict(client.models),
        "llm_calls": calls,
        "elapsed_sec": round(time.monotonic() - t0, 1),
        # Context 1건이 실제로 기다린 시간. `compose.py` 가 추정으로만 내던 값이다.
        "sec_per_context": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "max_sec_per_context": round(max(latencies), 3) if latencies else None,
    }


class CountingClient:
    """호출 수와 답한 모델을 센다. `_judge_n` 이 Context 1건당 정확히 n회 부르는지 본다."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.call_count = 0
        self.models: dict[str, int] = {}

    async def judge(self, text: str, candidates: list[dict]):
        self.call_count += 1
        result = await self._inner.judge(text, candidates)
        if result.model:
            self.models[result.model] = self.models.get(result.model, 0) + 1
        return result


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--start-rep", type=int, default=1)
    ap.add_argument("--matrix", default=str(ROOT / ".tau" / "matrix.json"))
    ap.add_argument("--outdir", default=str(ROOT / ".judge_vote" / "live"))
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--tau", type=float, default=0.30)
    ap.add_argument("--chain", default="openai:gpt-4o-mini")
    args = ap.parse_args()

    data = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    settings = get_settings()
    presets = await load_presets(settings)
    if not presets:
        raise SystemExit("프리셋이 0개다. DATABASE_URL 이 :15432 인지 보라 (T33).")

    chain = tuple(tuple(x.split(":", 1)) for x in args.chain.split(","))
    # n 은 설정 사본으로 주입한다 — 환경변수를 건드리면 lru_cache 된 설정이 어긋난다.
    voted = settings.model_copy(update={"judge_vote_n": args.n})
    log(f"  프리셋 {len(presets)}개 · 체인 {chain} · n={voted.judge_vote_n} · τ={args.tau}")
    log(f"  Context {len(data['contexts'])}건 · 회차 {args.start_rep}~{args.start_rep + args.reps - 1}")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    for rep in range(args.start_rep, args.start_rep + args.reps):
        out = outdir / f"live{args.n}-r{rep}.json"
        if out.exists():
            log(f"  r{rep} 이미 있음 — 건너뛴다 ({out.name})")
            continue
        log(f"  r{rep} 시작 …")
        client = CountingClient(
            LLMClient(gms_base_url=settings.gms_base_url, api_key=settings.gms_api_key,
                      chain=chain)
        )
        # db 는 쓰이지 않는다 — `_judge_n` 은 llm 과 settings 만 본다.
        svc = KeywordService(None, client, PresetCache(), voted)
        rec = await one_rep(svc, client, data, presets, args.k, args.tau, args.n)
        rec.update({"variant": f"live{args.n}", "rep": rep, "n": args.n,
                    "chain": [list(x) for x in chain], "tau": args.tau, "k": args.k})
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        rows = sum(len(v) for v in rec["selections"].values())
        log(f"  r{rep} 완료 — 선택 {rows}행 · LLM {rec['llm_calls']}회 · "
            f"실패 {len(rec['failures'])} · {rec['elapsed_sec']}s · "
            f"Context당 {rec['sec_per_context']}s (최대 {rec['max_sec_per_context']}s)")
        log(f"       → {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

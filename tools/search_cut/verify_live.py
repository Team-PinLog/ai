"""오프라인 재구성이 **실서버 응답과 정확히 같은지** 확인한다.

`tools/tau_grid/verify_reconstruction.py` 는 재구성이 근사였기 때문에 대조군이 필요했다
(같은 τ 로 다시 판정만 해도 Context 26% 가 흔들린다 — T39). **이쪽은 다르다.** 검색
경로에 LLM 이 없고 임베딩은 결정적이므로, 재구성과 실서버가 어긋나면 그것은 분산이
아니라 **구현이 명세와 다르다는 뜻**이다. 그래서 대조군 없이 정확 일치를 요구한다.

    python tools/search_cut/verify_live.py --ai http://127.0.0.1:8002

서버는 **이 브랜치 코드로** 띄워야 한다. 다른 워킹트리에서 띄운 서버를 재면 컷이 없는
코드를 재고 「구현이 안 먹는다」를 결론으로 낸다.

    .venv/Scripts/python.exe -m uvicorn app.main:app --port 8002 > .search/uvicorn.log 2>&1

`python -m uvicorn` 은 시스템 Python 을 타고 exit 0 으로 조용히 죽는다(T29).
로그는 파이프가 아니라 리디렉션으로 받는다 — 파이프는 프로세스가 사는 동안 0바이트다(T30).

GMS 임베딩을 질의 수만큼 부른다(요청당 1회, `personal-search.md §2`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


def reconstruct(results: list[dict], limit: int, floor: float, ratio: float) -> list[int]:
    """`SearchService._cut` 과 같은 규칙. **여기 다시 적는 것이 이 검증의 요점이다** —
    구현을 import 해 쓰면 구현이 명세와 달라도 둘이 함께 틀려 검증이 통과한다."""
    top = results[0]["sim"]
    return [
        r["record_id"]
        for r in results[:limit]
        if r["sim"] >= floor and r["sim"] >= ratio * top
    ]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", default="http://127.0.0.1:8002")
    ap.add_argument("--matrix", default=str(ROOT / ".search" / "matrix.json"))
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    data = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    settings = get_settings()
    floor, ratio = settings.search_similarity_floor, settings.search_top_ratio
    log(f"  서버 {args.ai} · τ_abs={floor} · r={ratio} · limit={args.limit}")

    cases = [(q, False) for q in data["queries"]] + [
        (q, True) for q in data.get("offtopic", [])
    ]
    ok = True
    async with httpx.AsyncClient(timeout=60.0) as client:
        for q, is_off in cases:
            resp = await client.post(
                f"{args.ai}/internal/v1/search",
                headers={"X-Internal-Secret": settings.internal_shared_secret},
                json={
                    "userId": q["user_id"],
                    "query": q["query"],
                    "limit": args.limit,
                    "embeddingProfile": settings.embedding_profile,
                },
            )
            if resp.status_code != 200:
                log(f"  [FAIL] '{q['query']}' → HTTP {resp.status_code}")
                ok = False
                continue
            live = [r["recordId"] for r in resp.json()["results"]]
            want = reconstruct(q["results"], args.limit, floor, ratio)
            match = live == want
            ok = ok and match
            tag = "무관" if is_off else "검증"
            log(
                f"  [{'PASS' if match else 'FAIL'}] [{tag}] {q['query'][:24]:<26} as={q['as']:<10} "
                f"실서버 {len(live):>2}건 · 재구성 {len(want):>2}건"
                + ("" if match else f"\n           실서버={live}\n           재구성={want}")
            )

    log(f"\n  종합: [{'PASS' if ok else 'FAIL'}]  {len(cases)}건")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

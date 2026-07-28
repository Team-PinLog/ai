"""파이프라인 실경로 드라이버 — Spring 역할을 대행한다.

PENDING 행 선삽입 → /context/process 호출 → 두 status COMPLETED 폴링.
FastAPI는 PENDING을 쓰지 않으므로(ai_state_repo 계약) 선삽입이 필수다.

사용:
  python tools/e2e/run_pipeline.py [--base http://localhost:8000]

이 스크립트는 벡터 컬럼을 읽지 않으므로 raw asyncpg로 충분하다.
벡터를 읽는 스크립트는 반드시 app.core.db.Database를 써야 한다(T23).
"""
from __future__ import annotations

import asyncio
import sys
import time

import asyncpg
import httpx

from _common import SETTINGS, base_url, headers, load_contexts

TIMEOUT_SEC = 180


async def main() -> None:
    base = base_url(sys.argv)
    ctxs = load_contexts()
    conn = await asyncpg.connect(SETTINGS.database_url)
    await conn.execute("SET search_path = ai, public")

    for c in ctxs:
        await conn.execute(
            "INSERT INTO ai.context_ai_state (context_id, embedding_status, keyword_status) "
            "VALUES ($1, 'PENDING', 'PENDING') ON CONFLICT (context_id) DO NOTHING",
            c["context_id"],
        )
    print(f"PENDING 행 {len(ctxs)}건 선삽입 완료 (Spring 대행)")

    async with httpx.AsyncClient(timeout=30.0) as client:
        for c in ctxs:
            r = await client.post(
                f"{base}/internal/v1/context/process",
                headers=headers(),
                json={
                    "contextId": c["context_id"],
                    "userId": c["user_id"],
                    "recordId": c["record_id"],
                    "text": c["text"],
                    "placeMeta": {"name": c["place"]},
                },
            )
            print(f"  ctx {c['context_id']} → HTTP {r.status_code}")

    ids = [c["context_id"] for c in ctxs]
    t0 = time.time()
    pending: list = []
    while time.time() - t0 < TIMEOUT_SEC:
        rows = await conn.fetch(
            "SELECT context_id, embedding_status, keyword_status FROM ai.context_ai_state "
            "WHERE context_id = ANY($1::bigint[]) ORDER BY context_id",
            ids,
        )
        pending = [
            r for r in rows
            if r["embedding_status"] != "COMPLETED" or r["keyword_status"] != "COMPLETED"
        ]
        if not pending:
            print(f"\n전 Context COMPLETED 도달 ({time.time() - t0:.1f}s)")
            break
        await asyncio.sleep(2)
    else:
        print("\n[TIMEOUT] 미완료 잔류:")
        for r in pending:
            print(f"  ctx {r['context_id']}: emb={r['embedding_status']} kw={r['keyword_status']}")

    await conn.close()


asyncio.run(main())

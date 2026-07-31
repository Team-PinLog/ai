"""측정용으로 로컬 DB 의 벡터 차원을 바꾸고, 끝나면 되돌린다.

    python tools/emb_grid/alter_dim.py --to 3072     # 조건 C·D 전
    python tools/emb_grid/alter_dim.py --to 1536     # 측정 후 원상 복구

**Flyway 마이그레이션을 만들지 않는다.** 이 티켓은 측정이고 차원 채택은 별건이다. 여기서
`V6__…sql` 을 만들면 아직 하지 않은 결정이 스키마 이력에 먼저 박히고, 되돌리려면 또 한 장을
쓰게 된다. 로컬 DB 에만 `ALTER` 로 걸고 복구 절차를 문서에 남기는 것이 이 측정의 계약이다.

**`ALTER` 가 가능한 이유는 벡터 인덱스가 없기 때문이다.** pgvector 의 `ivfflat`·`hnsw` 는
2000 차원까지만 색인하므로 인덱스가 걸려 있었다면 3072 로 못 갔다. 현재 `ai` 스키마의 인덱스는
btree 셋뿐이고 벡터 컬럼에는 아무것도 없다(전량 순차 스캔 — 37행이라 문제되지 않는다).

**기존 벡터는 전부 버린다.** 차원이 다른 값은 새 타입으로 캐스팅되지 않고, 컬럼이 `NOT NULL`
이라 비워 둘 수도 없다. 어차피 조건이 바뀌면 그 조건으로 다시 임베딩해야 하므로 손실이 아니다 —
다만 **실데이터가 들어 있는 DB 에서 돌리면 그것도 함께 사라진다.** 그래서 대상 DSN 을 먼저
찍고, 지울 행 수를 세어 보여준 뒤 진행한다.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402

TABLES = ("context_embedding", "keyword_preset")
ALLOWED = (1536, 3072)


def log(msg: str = "") -> None:
    print(msg, flush=True)


async def current_dim(conn, table: str) -> int | None:
    return await conn.fetchval(
        "SELECT a.atttypmod FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'ai' AND c.relname = $1 AND a.attname = 'embedding'",
        table,
    )


async def main() -> int:
    argv = sys.argv
    if "--to" not in argv:
        log(__doc__)
        return 2
    target = int(argv[argv.index("--to") + 1])
    if target not in ALLOWED:
        log(f"--to 는 {ALLOWED} 중 하나여야 한다 (받은 값: {target})")
        return 2

    settings = get_settings()
    # DSN 을 그대로 찍지 않는다 — 비밀번호가 들어 있다. 어느 DB 인지 알 만큼만 보여준다.
    where = re.sub(r"://[^@]*@", "://***@", settings.database_url)
    log(f"대상 DB   {where}")

    db = Database(settings.database_url)
    await db.connect()
    try:
        async with db.acquire() as conn:
            dims = {t: await current_dim(conn, t) for t in TABLES}
            log(f"현재 차원  " + " · ".join(f"ai.{t}=vector({d})" for t, d in dims.items()))
            if all(d == target for d in dims.values()):
                log(f"이미 vector({target}) 다. 아무것도 하지 않는다.")
                return 0

            emb_rows = await conn.fetchval("SELECT count(*) FROM ai.context_embedding")
            preset_rows = await conn.fetchval("SELECT count(*) FROM ai.keyword_preset")
            kw_rows = await conn.fetchval("SELECT count(*) FROM ai.context_keyword")
            log(
                f"버릴 것    context_embedding {emb_rows}행 · keyword_preset {preset_rows}행 "
                f"· context_keyword {kw_rows}행"
            )

        async with db.transaction() as conn:
            # context_keyword 가 keyword_preset 을 FK 로 잡으므로 자식부터 지운다.
            await conn.execute("DELETE FROM ai.context_keyword")
            await conn.execute("DELETE FROM ai.context_embedding")
            await conn.execute("DELETE FROM ai.keyword_preset")
            for table in TABLES:
                await conn.execute(
                    f"ALTER TABLE ai.{table} ALTER COLUMN embedding TYPE vector({target})"
                )

        async with db.acquire() as conn:
            after = {t: await current_dim(conn, t) for t in TABLES}
        log(f"변경 후    " + " · ".join(f"ai.{t}=vector({d})" for t, d in after.items()))
        if any(d != target for d in after.values()):
            log("차원이 목표와 다르다. 확인이 필요하다.")
            return 1
        log("\n프리셋이 비었다 — 다음 조건을 재기 전에 load_presets 가 다시 채운다")
        log("(run_condition.py 가 첫 단계로 부른다).")
        return 0
    finally:
        await db.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

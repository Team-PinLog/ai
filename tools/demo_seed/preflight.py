"""시딩을 시작하기 전에 **되돌릴 수 없는 일이 벌어지기 전에** 환경을 검사한다.

    python tools/demo_seed/preflight.py [--back URL] [--ai URL]

`seed.py`가 `--reset`보다 먼저 이것을 부른다. 단독 실행도 되며 그때는 아무것도
쓰지 않는다(§4의 인증 프로브가 만드는 행은 스스로 지운다).

## 왜 "시작 전"인가

세 결함이 공통으로 가진 성질은 **틀린 것이 즉시 드러나지 않는다**는 것이다.
드러날 때는 이미 `--reset`이 기존 데이터를 지운 뒤다.

    T28   reset 성공 → 첫 로그 출력에서 UnicodeEncodeError → member만 남고 Context 0건
    결함3  reset 성공 → 첫 POST에서 401 → 같은 상태

두 번 같은 모양으로 당했다. 그래서 검사는 **`reset()` 호출 이전**에 전부 끝난다.
하나라도 걸리면 시딩은 아무것도 지우지 않고 종료한다.

## 무엇을 검사하는가

    1. 어느 DB에 붙었는가        환경이 갈라진 것을 먼저 말한다
    2. 쓰기 컬럼 계약            back이 컬럼을 추가·삭제했는가 (결함 1)
    3. 미적용 back 마이그레이션   back이 스키마를 바꿔 들고 왔는가 (결함 1 보조)
    4. JWT 키가 back과 같은가    실제로 인증을 한 번 통과시켜 본다 (결함 3)
    5. 고아 ai.* 행              보고만 한다. 지우지 않는다 (결함 2)

5만 경고이고 나머지는 차단이다. 그 이유는 각 절에 적는다.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# `_client`는 import 시점에 `os.chdir()`·`get_settings()`를 하고, `.env`가 없으면
# 거기서 죽는다. 이 모듈의 판정 로직은 그것 없이 전부 성립하므로 **함수 안에서**
# 가져온다 — 방어를 일부러 어긋내 RED를 보는 것이 이 코드의 유일한 검증 수단이고,
# 그 테스트가 `.env`·DB·HTTP를 요구하면 실제로 돌지 않는다.

# ── 1. 쓰기 컬럼 계약 ───────────────────────────────────────────────────────
#
# 시딩이 **SQL로 직접 INSERT하는 테이블은 둘뿐**이다(`seed.py` 모듈 docstring).
# 나머지는 전부 back API를 타므로 back이 자기 컬럼을 책임진다.
#
# 각 컬럼을 셋 중 하나로 선언한다. 선언에 없는 컬럼이 실제 스키마에 나타나면
# **그것이 곧 back이 컬럼을 추가했다는 신호**이고, 시딩은 거기서 멈춘다.
#
# 이 검사가 겨눈 것은 NOT NULL 제약이 아니라 **우리가 값을 주지 않는 컬럼**이다.
# `email`은 V4에서 nullable로 태어났고 우리는 그 존재를 모른 채 NULL로 두었다.
# 며칠 뒤 back이 V6로 `SET NOT NULL`을 걸면서 back 기동이 죽었다. 제약이 걸리는
# 시점에는 이미 늦고, 컬럼이 생기는 시점에는 아직 아무 오류도 나지 않는다.
# 그 사이를 이 선언이 메운다.
SEED = "seed"  # 시딩이 값을 넣는다
DB = "db"  # IDENTITY·DEFAULT에 맡긴다
NULL = "null"  # 의도적으로 비운다

WRITE_CONTRACT: dict[str, dict[str, str]] = {
    "core.member": {
        "id": DB,  # GENERATED ALWAYS AS IDENTITY
        "created_at": DB,  # DEFAULT now()
        "deleted_at": NULL,  # 활성 회원
    },
    "core.social_account": {
        "id": DB,
        "member_id": SEED,
        "provider": SEED,  # DEMO_PROVIDER — reset의 삭제 범위를 정하는 표식
        "provider_user_id": SEED,
        "email": SEED,  # V6가 NOT NULL로 만들었다. 값의 근거는 bootstrap_members
        "created_at": DB,
        "deleted_at": NULL,
    },
}


def diff_write_contract(
    table: str,
    declared: dict[str, str],
    actual: dict[str, tuple[bool, bool]],
) -> list[str]:
    """계약과 실제 스키마의 차이를 사람이 읽을 문장으로 만든다.

    `actual`은 `{컬럼명: (nullable, has_default)}`.

    순수 함수로 둔 이유는 **일부러 어긋내 RED를 보는 것**이 이 방어의 유일한
    검증 수단이기 때문이다. DB 없이 테스트에서 세 갈래를 전부 재현할 수 있어야
    한다(`tests/test_demo_seed_preflight.py`).
    """
    problems: list[str] = []

    for col in sorted(set(actual) - set(declared)):
        nullable, has_default = actual[col]
        problems.append(
            f"{table}: 계약에 없는 컬럼 `{col}` "
            f"(nullable={nullable}, default={has_default}) — back이 컬럼을 추가했다. "
            f"시딩이 값을 넣을지 정하고 WRITE_CONTRACT에 선언하라. "
            f"NULL로 두면 back이 나중에 NOT NULL을 걸 때 기동이 죽는다(V6 전례)"
        )

    for col in sorted(set(declared) - set(actual)):
        problems.append(
            f"{table}: 계약에 있으나 실제 스키마에 없는 컬럼 `{col}` — "
            f"back이 지웠거나 이름을 바꿨다. 시딩 INSERT가 실패한다"
        )

    for col in sorted(set(declared) & set(actual)):
        nullable, has_default = actual[col]
        if declared[col] in (DB, NULL) and not nullable and not has_default:
            problems.append(
                f"{table}: `{col}`을(를) {declared[col]}로 선언했으나 실제는 "
                f"NOT NULL이고 기본값이 없다 — 시딩 INSERT가 실패한다"
            )

    return problems


async def check_write_contract(conn) -> list[str]:
    problems: list[str] = []
    for table, declared in WRITE_CONTRACT.items():
        schema, name = table.split(".")
        # `has_default`는 "우리가 값을 주지 않아도 채워지는가"다. `column_default`만
        # 보면 안 된다 — `GENERATED ALWAYS AS IDENTITY`는 default가 아니라 별도
        # 속성이라 `column_default`가 NULL이고, 그래서 `id`가 "NOT NULL인데 기본값이
        # 없다"로 오탐된다. 단위 테스트는 이 쿼리를 타지 않으므로 잡히지 않았고
        # 실제 스키마에 처음 돌렸을 때 드러났다.
        rows = await conn.fetch(
            "SELECT column_name, is_nullable, "
            "       (column_default IS NOT NULL "
            "        OR is_identity = 'YES' "
            "        OR is_generated = 'ALWAYS') AS has_default "
            "FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2",
            schema,
            name,
        )
        if not rows:
            problems.append(
                f"{table}: 테이블이 없다 — back 마이그레이션이 적용되지 않은 DB다. "
                f"§1의 접속 대상을 확인하라"
            )
            continue
        actual = {
            r["column_name"]: (r["is_nullable"] == "YES", r["has_default"]) for r in rows
        }
        problems += diff_write_contract(table, declared, actual)
    return problems


# ── 2. 미적용 back 마이그레이션 ─────────────────────────────────────────────


def _back_repo() -> Path | None:
    """back 레포의 위치. 없으면 None.

    `shared_root()`를 기준으로 삼는다 — worktree 안에서는 `ai/.claude/worktrees/*`가
    루트라 형제 디렉터리 계산이 틀린다.
    """
    from _client import shared_root

    env = os.environ.get("PINLOG_BACK_REPO")
    if env:
        p = Path(env)
        return p if (p / "src/main/resources/db/migration").is_dir() else None
    p = shared_root().parent / "back"
    return p if (p / "src/main/resources/db/migration").is_dir() else None


def pending_migrations(applied: set[str], files: list[str]) -> list[str]:
    """back 레포에 있으나 이 DB에 적용되지 않은 마이그레이션 파일명."""
    return sorted(set(files) - applied)


# 시딩이 직접 INSERT하는 테이블. 미적용 마이그레이션이 이 이름을 건드리면 강조한다.
_OUR_TABLES = tuple(WRITE_CONTRACT)


async def check_pending_migrations(conn) -> list[str]:
    """미적용 마이그레이션을 **경고**한다. 단, 우리 테이블을 건드리면 차단이다.

    back 레포가 없으면(CI·배포 환경) 조용히 건너뛴다 — 이 검사는 로컬 시연
    도구의 편의이지 계약이 아니다.

    왜 파일 이름과 본문 문자열만 보는가: SQL을 파싱해 제약을 해석하는 것은
    취약하고, 그럴 필요도 없다. `V6__social_account_email_not_null.sql`은
    **이름만으로 충분히 말한다**. 판단은 사람이 한다.
    """
    repo = _back_repo()
    if repo is None:
        return []

    mig = repo / "src/main/resources/db/migration"
    files = sorted(p.name for p in mig.glob("V*__*.sql"))
    try:
        applied = {
            r["script"]
            for r in await conn.fetch(
                "SELECT script FROM public.flyway_schema_history WHERE success"
            )
        }
    except Exception:  # noqa: BLE001 — 테이블 자체가 없는 DB
        applied = set()

    pending = pending_migrations(applied, files)
    if not pending:
        return []

    touching = [
        f
        for f in pending
        if any(t.split(".")[1] in (mig / f).read_text(encoding="utf-8") for t in _OUR_TABLES)
    ]
    lines = [
        f"back에 미적용 마이그레이션 {len(pending)}개: {', '.join(pending)} — "
        f"back을 기동하면 지금 DB에 적용된다"
    ]
    if touching:
        lines.append(
            f"그중 {', '.join(touching)} 는 시딩이 직접 쓰는 테이블"
            f"({', '.join(_OUR_TABLES)})을 언급한다 — 먼저 back을 기동해 스키마를 "
            f"맞춘 뒤 시딩하라. 순서를 뒤집으면 이번에 넣은 행이 그 마이그레이션을 막는다"
        )
    return lines if touching else []  # 우리 테이블과 무관하면 경고로만


# ── 3. JWT 키 실검증 ────────────────────────────────────────────────────────

_PROBE_USER = "__preflight__"


async def check_back_auth(db, back: str, pem: bytes) -> list[str]:
    """back이 이 키로 서명한 토큰을 실제로 받아들이는지 **한 번 통과시켜 본다**.

    키 경로를 하나로 묶는 것(`_client.shared_root`)과 이 검사는 다른 일을 한다.
    경로를 묶어도 back에 주입된 `JWT_PRIVATE_KEY`가 다른 키면 여전히 401이고,
    그것은 우리 파일 배치로 막을 수 있는 것이 아니다. 실제로 통과시켜 보는 것만이
    "같은 키인가"에 답한다.

    프로브 member는 여기서 만들고 여기서 지운다. `demo-seed` provider를 쓰되
    `provider_user_id`가 `__preflight__`라 `demo_data.yaml`의 어떤 key와도 겹치지
    않는다 — 겹치면 부분 유니크 인덱스에 걸린다.
    """
    from _client import DEMO_PROVIDER, BackClient

    member_id: int | None = None
    try:
        async with db.transaction() as conn:
            await _drop_probe(conn)
            member_id = await conn.fetchval(
                "INSERT INTO core.member DEFAULT VALUES RETURNING id"
            )
            await conn.execute(
                "INSERT INTO core.social_account "
                "(member_id, provider, provider_user_id, email) VALUES ($1, $2, $3, $4)",
                member_id,
                DEMO_PROVIDER,
                _PROBE_USER,
                f"{_PROBE_USER}@{DEMO_PROVIDER}.invalid",
            )

        with BackClient(back, member_id, pem) as c:
            c.get("/v1/collections?size=1")
    except Exception as e:  # noqa: BLE001
        return [
            f"back 인증 프로브 실패 — {type(e).__name__}: {e}\n"
            f"      시딩이 만드는 모든 요청이 401이 된다. 확인 순서:\n"
            f"      1) back이 떠 있는가 ({back})\n"
            f"      2) back의 JWT_PRIVATE_KEY와 이 키가 같은 파일인가\n"
            f"         현재 키: {os.environ.get('PINLOG_DEMO_JWT_KEY', '(기본 경로)')}\n"
            f"      3) worktree에서 돌리고 있지 않은가 — 키는 메인 워킹트리의 "
            f".demo/ 하나를 공유한다"
        ]
    finally:
        if member_id is not None:
            async with db.transaction() as conn:
                await _drop_probe(conn)
    return []


async def _drop_probe(conn) -> None:
    from _client import DEMO_PROVIDER

    ids = [
        r["member_id"]
        for r in await conn.fetch(
            "SELECT member_id FROM core.social_account "
            "WHERE provider = $1 AND provider_user_id = $2",
            DEMO_PROVIDER,
            _PROBE_USER,
        )
    ]
    if not ids:
        return
    await conn.execute(
        "DELETE FROM core.social_account WHERE provider = $1 AND provider_user_id = $2",
        DEMO_PROVIDER,
        _PROBE_USER,
    )
    await conn.execute("DELETE FROM core.member WHERE id = ANY($1::bigint[])", ids)


# ── 4. 고아 ai.* 행 ─────────────────────────────────────────────────────────

# `context_id`로 `core.context`를 가리키는 `ai` 테이블 전부.
#
# FK가 없다(`V100__ai_tables.sql`). 스키마 소유가 갈려 있어 참조 무결성을 DB가
# 지켜 주지 않으므로, 여기 목록에서 빠진 테이블은 **영원히 아무도 세지 않는다**.
# 실제로 `context_keyword_analysis`가 그렇게 259행 중 222행이 고아가 됐다.
ORPHAN_TABLES = (
    "ai.context_ai_state",
    "ai.context_embedding",
    "ai.context_keyword",
    "ai.context_keyword_analysis",
)


async def count_orphans(conn) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in ORPHAN_TABLES:
        out[t] = await conn.fetchval(
            f"SELECT count(*) FROM {t} o "  # noqa: S608 — 상수 목록
            "WHERE NOT EXISTS (SELECT 1 FROM core.context c WHERE c.id = o.context_id)"
        )
    return out


async def delete_orphans(conn) -> dict[str, int]:
    """`--prune-orphans`가 명시됐을 때만 불린다. 삭제 순서는 무관하다(FK 없음)."""
    out: dict[str, int] = {}
    for t in ORPHAN_TABLES:
        tag = await conn.execute(
            f"DELETE FROM {t} o "  # noqa: S608 — 상수 목록
            "WHERE NOT EXISTS (SELECT 1 FROM core.context c WHERE c.id = o.context_id)"
        )
        out[t] = int(tag.rsplit(" ", 1)[-1])
    return out


def format_orphans(counts: dict[str, int]) -> list[str]:
    """고아를 **보고만** 한다. 지우지 않는 근거는 `--prune-orphans` 도움말과 같다."""
    total = sum(counts.values())
    if total == 0:
        return []
    lines = [f"고아 ai.* 행 {total}개 — 참조하는 core.context가 없다"]
    for t, n in counts.items():
        if n:
            lines.append(f"      {t:<32} {n}")
    lines.append(
        "      지우지 않았다. 이 행들이 이번 시딩의 산출물과 섞여 집계를 틀리게 만든다면"
    )
    lines.append(
        "      --prune-orphans 로 지워라. 남의 측정 데이터일 수 있으므로 자동 삭제하지 않는다"
    )
    return lines


# ── 실행 ────────────────────────────────────────────────────────────────────


def _dsn_label(url: str) -> str:
    """비밀번호를 지운 접속 대상. 어느 DB에 붙었는지가 첫 줄에 보여야 한다.

    로컬에 pgvector 컨테이너가 둘 있고(`:15432` 시연 정본 · `:5433` 07-27 하네스
    잔재) `.env`의 기본값은 후자를 가리킨다. `-174` §7 절차는 매 명령에
    `DATABASE_URL`을 덮어쓰지만, 한 번 빠뜨리면 도구가 조용히 다른 DB에 붙는다.
    """
    return re.sub(r"://([^:/]+):[^@]*@", r"://\1:***@", url)


async def run(db, url: str, back: str, pem: bytes, log=print) -> bool:
    """전부 통과하면 True. 하나라도 걸리면 False — 호출자는 아무것도 지우지 마라."""
    log("preflight")
    log(f"  DB   {_dsn_label(url)}")

    problems: list[str] = []
    warnings: list[str] = []

    async with db.acquire() as conn:
        problems += await check_write_contract(conn)
        problems += await check_pending_migrations(conn)

    if problems:
        for p in problems:
            log(f"  [BLOCK] {p}")
        log("\n  시딩을 시작하지 않았다. 아무것도 지우지 않았다.")
        return False
    log(f"  [ok] 쓰기 컬럼 계약 — {', '.join(WRITE_CONTRACT)}")

    auth = await check_back_auth(db, back, pem)
    if auth:
        for p in auth:
            log(f"  [BLOCK] {p}")
        log("\n  시딩을 시작하지 않았다. 아무것도 지우지 않았다.")
        return False
    log(f"  [ok] back 인증 프로브 — {back}")

    async with db.acquire() as conn:
        warnings += format_orphans(await count_orphans(conn))
    for w in warnings:
        log(f"  [WARN] {w}" if not w.startswith("      ") else w)
    if not warnings:
        log("  [ok] 고아 ai.* 행 없음")
    return True


if __name__ == "__main__":
    import asyncio
    import sys

    from _client import SETTINGS, ai_base, back_base, ensure_key  # noqa: F401

    from app.core.db import Database

    async def _main() -> int:
        db = Database(SETTINGS.database_url)
        await db.connect()
        try:
            ok = await run(db, SETTINGS.database_url, back_base(sys.argv), ensure_key())
        finally:
            await db.disconnect()
        return 0 if ok else 2

    sys.exit(asyncio.run(_main()))

"""발표 시연용 데모 데이터 시딩 — back API 경로로 만든다.

    python tools/demo_seed/seed.py [--reset] [--pace 20] [--back URL] [--ai URL]

## 왜 back API인가 (직접 INSERT가 아니라)

Context 본문·Place·Collection은 `core` 스키마이고 back이 소유한다. SQL로 직접
넣으면 **데이터는 있는데 파이프라인은 돌지 않은** 상태가 만들어진다 — 화면에는
나오지만 `-102`가 붙인 `PENDING` 생성도, FastAPI 호출도 일어나지 않는다.

API로 만들면 그 경로가 실제로 탄다. 그래서 이 스크립트는 시딩이면서 동시에
**back↔ai 통합 검증**이다. Record 하나를 만들면 다음이 순서대로 일어난다.

    POST /v1/records
      → core.place·record·context INSERT (back)
      → ai.context_ai_state PENDING INSERT (back, 커밋 전)
      → POST /internal/v1/context/process (back → FastAPI, fire-and-forget)
      → 임베딩·판정 → ai.context_embedding·context_keyword (FastAPI)

## SQL을 쓰는 두 지점과 그 근거

1. **member 생성** — back의 유일한 회원 생성 경로가 소셜 OAuth 콜백이라
   (`SocialLoginService`) 스크립트가 부를 API가 없다. `core.member`는 컬럼이
   id·created_at·deleted_at뿐인 익명 테이블이라 이 INSERT가 도메인 규칙을
   우회하지 않는다. 추적을 위해 `core.social_account`에 provider `demo-seed`를
   같이 남긴다 — 이 표식이 `--reset`의 삭제 범위를 정한다.
2. **`--reset` 삭제** — API의 삭제는 전부 soft delete다. "DB를 비우고 다시"를
   재현하려면 hard delete가 필요하고 그에 해당하는 API가 없다. 삭제 범위는
   provider `demo-seed`로 식별된 member의 데이터로 한정한다.

그 외 Record·Context·Collection·Follow는 전부 API로 만든다.

## GMS 429와 pace

판정(Gemini)은 GMS 게이트웨이에서 지속적으로 분당 2건 안팎만 통과한다
(2026-07-29 실측). Record를 몰아서 만들면 판정이 무더기로 429를 받고, 그때
keyword_status는 `PROCESSING`으로 남아 재스캔을 기다린다. 로컬에는 재스캔이
없으므로 이 스크립트가 그 역할을 대신한다 — `--pace` 간격으로 생성한 뒤,
미완료 건을 **한 건씩** 되살린다.
"""
from __future__ import annotations

import asyncio
import sys
import time

import httpx

from _client import (
    DEMO_PROVIDER,
    SETTINGS,
    BackClient,
    ai_base,
    back_base,
    ensure_key,
    load_data,
)

from app.core.db import Database

# 미완료 건 회수의 상한. 14건 × (429 재시도 포함) 여유를 본 값이다.
RECOVER_DEADLINE_SEC = 1800
# 한 건을 되살린 뒤 다음 건까지 쉬는 시간. 판정 통과율(분당 2건)에 맞춘다.
RECOVER_INTERVAL_SEC = 20


def _make_stdout_utf8_safe() -> None:
    """콘솔 인코딩이 UTF-8이 아니어도 로그 한 줄 때문에 시딩이 죽지 않게 한다.

    2026-07-30 실측 — 백그라운드 실행에서 stdout이 cp949로 잡혀 `—`(em-dash) 한
    글자에 UnicodeEncodeError가 났다. **`--reset`이 이미 기존 데이터를 지운 뒤
    첫 로그 출력에서 죽어**, DB에 member만 남고 Context가 0건인 상태가 됐다.
    GMS 호출 전이라 비용 손실은 없었지만 복구는 전량 재시딩이었다.

    호출자가 `PYTHONIOENCODING=utf-8`을 기억해야 하는 구조를 두지 않는다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # 파이프로 감싸여 reconfigure가 없는 경우. 아래 log()가 받아낸다.
            pass


_make_stdout_utf8_safe()


def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # 최후 방어. 로그가 시딩을 죽이는 것보다 글자가 깨지는 편이 낫다.
        enc = sys.stdout.encoding or "ascii"
        print(msg.encode(enc, errors="replace").decode(enc), flush=True)


# ── reset ──────────────────────────────────────────────────────────────────


async def reset(db: Database) -> None:
    """provider `demo-seed`로 표시된 member의 데이터를 전부 hard delete.

    FK 역순으로 지운다. `ai.*`는 `core.context`를 참조하지 않지만(FK 없음)
    남겨 두면 다음 시딩의 새 context_id와 겹치지 않을 뿐 쓰레기로 쌓이므로
    같이 지운다.
    """
    async with db.acquire() as conn:
        member_ids = [
            r["member_id"]
            for r in await conn.fetch(
                "SELECT member_id FROM core.social_account WHERE provider = $1",
                DEMO_PROVIDER,
            )
        ]
        if not member_ids:
            log("reset: 기존 데모 데이터 없음")
            return

        ctx_ids = [
            r["id"]
            for r in await conn.fetch(
                "SELECT id FROM core.context WHERE member_id = ANY($1::bigint[])",
                member_ids,
            )
        ]
        async with conn.transaction():
            if ctx_ids:
                await conn.execute(
                    "DELETE FROM ai.context_keyword WHERE context_id = ANY($1::bigint[])",
                    ctx_ids,
                )
                await conn.execute(
                    "DELETE FROM ai.context_embedding WHERE context_id = ANY($1::bigint[])",
                    ctx_ids,
                )
                await conn.execute(
                    "DELETE FROM ai.context_ai_state WHERE context_id = ANY($1::bigint[])",
                    ctx_ids,
                )
            await conn.execute(
                "DELETE FROM core.feed_event WHERE member_id = ANY($1::bigint[])",
                member_ids,
            )
            await conn.execute(
                "DELETE FROM core.collection_record WHERE collection_id IN "
                "(SELECT id FROM core.collection WHERE member_id = ANY($1::bigint[]))",
                member_ids,
            )
            await conn.execute(
                "DELETE FROM core.collection WHERE member_id = ANY($1::bigint[])",
                member_ids,
            )
            await conn.execute(
                "DELETE FROM core.follow WHERE follower_member_id = ANY($1::bigint[]) "
                "OR followee_member_id = ANY($1::bigint[])",
                member_ids,
            )
            await conn.execute(
                "DELETE FROM core.context WHERE member_id = ANY($1::bigint[])", member_ids
            )
            await conn.execute(
                "DELETE FROM core.record WHERE member_id = ANY($1::bigint[])", member_ids
            )
            # place는 공용 스냅샷이라 member에 속하지 않는다. 데모가 만든 것만 지운다.
            await conn.execute(
                "DELETE FROM core.place WHERE kakao_place_id LIKE 'demo-seed-%'"
            )
            await conn.execute(
                "DELETE FROM core.social_account WHERE provider = $1", DEMO_PROVIDER
            )
            await conn.execute(
                "DELETE FROM core.member WHERE id = ANY($1::bigint[])", member_ids
            )
    log(f"reset: member {len(member_ids)}명 · context {len(ctx_ids)}건 삭제")


# ── member 부트스트랩 ───────────────────────────────────────────────────────


async def bootstrap_members(db: Database, keys: list[str]) -> dict[str, int]:
    """member 행을 만들고 `demo-seed` social_account로 표시한다."""
    ids: dict[str, int] = {}
    async with db.transaction() as conn:
        for key in keys:
            member_id = await conn.fetchval(
                "INSERT INTO core.member DEFAULT VALUES RETURNING id"
            )
            await conn.execute(
                "INSERT INTO core.social_account (member_id, provider, provider_user_id) "
                "VALUES ($1, $2, $3)",
                member_id,
                DEMO_PROVIDER,
                key,
            )
            ids[key] = member_id
    log(f"member {len(ids)}명 생성: " + ", ".join(f"{k}={v}" for k, v in ids.items()))
    return ids


# ── 파이프라인 회수 ────────────────────────────────────────────────────────


async def pipeline_status(db: Database, ctx_ids: list[int]) -> list[dict]:
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT context_id, embedding_status, keyword_status "
            "FROM ai.context_ai_state WHERE context_id = ANY($1::bigint[]) "
            "ORDER BY context_id",
            ctx_ids,
        )
    return [dict(r) for r in rows]


async def recover(db: Database, ai: str, contexts: dict[int, dict]) -> bool:
    """미완료 Context를 한 건씩 되살린다. 전부 COMPLETED면 True.

    `PROCESSING` 잔류는 판정이 429로 중단된 흔적이다. `try_start`는 stale
    `PROCESSING`을 `PROCESSING_EXPIRY_SEC`(기본 600초) 뒤에만 재선점하므로,
    그대로 다시 호출하면 10분 동안 아무 일도 일어나지 않는다. 상태를 `PENDING`
    으로 되돌려 즉시 재선점 가능하게 만든다 — 운영의 M3 재처리
    (`COMPLETED → PENDING`, state-machine.md)와 같은 성격의 쓰기이며 `ai` 스키마
    안에서 끝난다.
    """
    ids = list(contexts)
    t0 = time.time()
    async with httpx.AsyncClient(timeout=30.0) as client:
        while time.time() - t0 < RECOVER_DEADLINE_SEC:
            rows = await pipeline_status(db, ids)
            todo = [
                r
                for r in rows
                if r["embedding_status"] != "COMPLETED"
                or r["keyword_status"] != "COMPLETED"
            ]
            done = len(rows) - len(todo)
            if not todo:
                if len(rows) == len(ids):
                    log(f"  파이프라인 {done}/{len(ids)} COMPLETED ({time.time() - t0:.0f}s)")
                    return True
                # 상태 행 자체가 없다 = back의 PENDING INSERT가 빠졌다는 뜻이고,
                # 그건 회수로 고칠 수 있는 상태가 아니다. 계약 위반이므로 멈춘다.
                missing = sorted(set(ids) - {r["context_id"] for r in rows})
                log(
                    f"  [ABORT] ai.context_ai_state 행이 없는 Context {len(missing)}건: "
                    f"{missing[:10]} — back이 PENDING을 넣지 않았다"
                )
                return False

            target = todo[0]
            ctx_id = target["context_id"]
            async with db.acquire() as conn:
                await conn.execute(
                    "UPDATE ai.context_ai_state "
                    "SET embedding_status = CASE WHEN embedding_status = 'PROCESSING' "
                    "      THEN 'PENDING' ELSE embedding_status END, "
                    "    keyword_status = CASE WHEN keyword_status = 'PROCESSING' "
                    "      THEN 'PENDING' ELSE keyword_status END, "
                    "    updated_at = now() "
                    "WHERE context_id = $1",
                    ctx_id,
                )
            req = contexts[ctx_id]
            await client.post(
                f"{ai}/internal/v1/context/process",
                headers={"X-Internal-Secret": SETTINGS.internal_shared_secret},
                json=req,
            )
            log(
                f"  회수 ctx={ctx_id} (emb={target['embedding_status']} "
                f"kw={target['keyword_status']}) · 남은 {len(todo)}건 · "
                f"완료 {done}/{len(ids)}"
            )
            await asyncio.sleep(RECOVER_INTERVAL_SEC)

    log(f"  [TIMEOUT] {RECOVER_DEADLINE_SEC}s 안에 완료되지 않았다")
    return False


# ── main ───────────────────────────────────────────────────────────────────


async def main() -> int:
    argv = sys.argv
    back = back_base(argv)
    ai = ai_base(argv)
    pace = float(argv[argv.index("--pace") + 1]) if "--pace" in argv else 20.0
    data = load_data()
    pem = ensure_key()

    db = Database(SETTINGS.database_url)
    await db.connect()
    try:
        if "--reset" in argv:
            await reset(db)

        members = data["members"]
        ids = await bootstrap_members(db, [m["key"] for m in members])

        total_records = sum(len(m["records"]) for m in members)
        log(
            f"\nRecord {total_records}건 생성 (pace {pace:.0f}s) — "
            f"각 건이 임베딩 1 + 판정 1 GMS 호출을 유발한다"
        )

        record_ids: dict[str, dict[str, int]] = {}
        # 회수 때 재전송할 /context/process 본문. back이 보내는 것과 같은 형태다.
        process_bodies: dict[int, dict] = {}
        n = 0
        for m in members:
            record_ids[m["key"]] = {}
            with BackClient(back, ids[m["key"]], pem) as c:
                for rec in m["records"]:
                    p = rec["place"]
                    resp = c.post(
                        "/v1/records",
                        {
                            "place": {
                                "kakaoPlaceId": p["kakao_place_id"],
                                "name": p["name"],
                                "address": p["address"],
                                "lat": p["lat"],
                                "lng": p["lng"],
                            },
                            "contextBody": rec["context"],
                        },
                    )
                    rid = resp["recordId"]
                    ctx_id = resp["contexts"][0]["contextId"]
                    record_ids[m["key"]][rec["key"]] = rid
                    process_bodies[ctx_id] = {
                        "contextId": ctx_id,
                        "userId": ids[m["key"]],
                        "recordId": rid,
                        "text": rec["context"],
                        "placeMeta": {"name": p["name"]},
                    }
                    n += 1
                    log(f"  [{n}/{total_records}] {m['key']}/{rec['key']} → record={rid} context={ctx_id}")
                    if n < total_records:
                        await asyncio.sleep(pace)

        log("\nCollection 생성 (GMS 호출 없음 — Record 재사용)")
        collection_ids: dict[str, list[int]] = {}
        for m in members:
            collection_ids[m["key"]] = []
            with BackClient(back, ids[m["key"]], pem) as c:
                for col in m.get("collections", []):
                    resp = c.post(
                        "/v1/collections",
                        {
                            "title": col["title"],
                            "recordIds": [
                                record_ids[m["key"]][k] for k in col["records"]
                            ],
                        },
                    )
                    cid = resp["collectionId"]
                    collection_ids[m["key"]].append(cid)
                    log(f"  {m['key']}: {col['title']} → collection={cid} ({len(col['records'])} records)")

        log("\nFollow 생성")
        for m in members:
            targets = m.get("follows", [])
            if not targets:
                continue
            with BackClient(back, ids[m["key"]], pem) as c:
                for t in targets:
                    # follow는 Shelf 단위이며 대상 Collection 하나로 지정한다.
                    c.post("/v1/follows", {"collectionId": collection_ids[t][0]})
                    log(f"  {m['key']} → {t}")

        log("\n파이프라인 완료 대기 (미완료 건은 한 건씩 회수)")
        ok = await recover(db, ai, process_bodies)

        rows = await pipeline_status(db, list(process_bodies))
        async with db.acquire() as conn:
            kw = await conn.fetchval(
                "SELECT count(*) FROM ai.context_keyword WHERE context_id = ANY($1::bigint[])",
                list(process_bodies),
            )
        log(
            f"\n결과: Context {len(rows)}건 · "
            f"COMPLETED {sum(1 for r in rows if r['embedding_status'] == 'COMPLETED' and r['keyword_status'] == 'COMPLETED')}건 · "
            f"Keyword {kw}행"
        )
        log("검증은 다음으로: python tools/demo_seed/verify.py")
        return 0 if ok else 1
    finally:
        await db.disconnect()


sys.exit(asyncio.run(main()))

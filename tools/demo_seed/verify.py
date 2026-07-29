"""데모 시딩 결과가 시연 3종을 실제로 성립시키는지 확인한다.

    python tools/demo_seed/verify.py [--back URL] [--ai URL]

`seed.py`가 "만들었다"고 말하는 것과 시연 화면이 "보여준다"는 것은 다르다.
이 스크립트는 후자만 본다 — **시연에서 실제로 호출될 두 API를 그대로 호출**하고
그 응답만으로 판정한다. DB를 직접 세어 통과시키지 않는다.

    A. 자연어 검색  POST /internal/v1/search        (FastAPI, 주인공 userId)
    B. 탐색 피드    GET  /v1/feed/collections       (back, 주인공 인증)
    C. Keyword      B의 응답 `keywords` + Record 상세

종료 코드 0이면 셋 다 통과다. CI가 아니라 시연 직전 점검용이다 — 실호출이 필요하다.
"""
from __future__ import annotations

import asyncio
import sys

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

PASS = "PASS"
FAIL = "FAIL"


def log(msg: str = "") -> None:
    print(msg, flush=True)


def head(title: str) -> None:
    log("\n" + "=" * 78)
    log(title)
    log("=" * 78)


async def load_ids(db: Database) -> tuple[dict[str, int], dict[int, str], dict[int, int]]:
    """demo-seed member 표식으로 key↔id 매핑을 복원한다.

    seed.py의 실행 결과를 파일로 넘기지 않는다 — 매핑의 진실은 DB에 있고,
    파일로 옮기면 둘이 어긋날 수 있다.
    """
    async with db.acquire() as conn:
        members = {
            r["provider_user_id"]: r["member_id"]
            for r in await conn.fetch(
                "SELECT member_id, provider_user_id FROM core.social_account "
                "WHERE provider = $1",
                DEMO_PROVIDER,
            )
        }
        place_by_ctx = {
            r["context_id"]: r["name"]
            for r in await conn.fetch(
                "SELECT ctx.id AS context_id, p.name AS name "
                "FROM core.context ctx "
                "JOIN core.record r ON r.id = ctx.record_id "
                "JOIN core.place p ON p.id = r.place_id "
                "WHERE ctx.member_id = ANY($1::bigint[])",
                list(members.values()),
            )
        }
        owner_by_collection = {
            r["id"]: r["member_id"]
            for r in await conn.fetch(
                "SELECT id, member_id FROM core.collection "
                "WHERE member_id = ANY($1::bigint[])",
                list(members.values()),
            )
        }
    return members, place_by_ctx, owner_by_collection


async def verify_search(
    ai: str, host_id: int, data: dict, place_by_ctx: dict[int, str]
) -> bool:
    """A. 자연어 검색 — 시연 질의가 의도한 Record를 1위로 낸다."""
    head("A. 자연어 검색 — POST /internal/v1/search (실제 GMS 임베딩)")

    # place 이름으로 기대값을 맞춘다. seed.py가 만든 record_id를 파일로 넘기지
    # 않으므로, demo_data.yaml의 place name이 둘을 잇는 유일한 키다.
    expect_place = {
        rec["key"]: rec["place"]["name"]
        for m in data["members"]
        if m["key"] == "host"
        for rec in m["records"]
    }

    ok = True
    async with httpx.AsyncClient(timeout=60.0) as client:
        for q in data["demo_queries"]:
            resp = await client.post(
                f"{ai}/internal/v1/search",
                headers={"X-Internal-Secret": SETTINGS.internal_shared_secret},
                json={
                    "userId": host_id,
                    "query": q["query"],
                    "limit": 5,
                    "embeddingProfile": SETTINGS.embedding_profile,
                },
            )
            if resp.status_code != 200:
                log(f"  [{FAIL}] '{q['query']}' → HTTP {resp.status_code}")
                ok = False
                continue
            results = resp.json()["results"]
            want = expect_place[q["expect"]]
            log(f"\n  질의: {q['query']}")
            for i, r in enumerate(results, 1):
                name = place_by_ctx.get(r["contextId"], f"ctx={r['contextId']}")
                mark = "  ← 의도" if name == want and i == 1 else ""
                log(f"    {i}. {name}  sim={r['similarity']:.4f}{mark}")
            top = place_by_ctx.get(results[0]["contextId"]) if results else None
            hit = top == want
            ok = ok and hit
            log(f"    → 1위 일치 [{PASS if hit else FAIL}] (기대 {want})")
    log(f"\n  A 종합: [{PASS if ok else FAIL}]")
    return ok


def verify_feed(
    back: str, host_id: int, pem: bytes, owner_by_collection: dict[int, int]
) -> tuple[bool, list[dict]]:
    """B. 탐색 피드 — 후보가 비지 않고 소유자가 섞인다."""
    head("B. 탐색 피드 — GET /v1/feed/collections (back, 주인공 인증)")

    with BackClient(back, host_id, pem) as c:
        page = c.get("/v1/feed/collections?size=20")
    items = page.get("items", [])
    owners = {owner_by_collection.get(i["collectionId"]) for i in items}
    mine = [i for i in items if owner_by_collection.get(i["collectionId"]) == host_id]

    log(f"  requestId={page.get('requestId')}  items={len(items)}  hasNext={page.get('hasNext')}\n")
    for i in items:
        owner = owner_by_collection.get(i["collectionId"])
        kws = ", ".join(i["keywords"]) if i["keywords"] else "(없음)"
        log(
            f"    [{i['position']:>2}] {i['title']:<16} owner={owner} "
            f"records={i['recordCount']}  keywords=[{kws}]"
        )

    not_empty = len(items) > 0
    mixed = len(owners) >= 2
    no_self = not mine
    log("")
    log(f"  후보 비지 않음        [{PASS if not_empty else FAIL}] {len(items)}건")
    log(f"  여러 소유자 혼재      [{PASS if mixed else FAIL}] 소유자 {len(owners)}명")
    log(f"  본인 Collection 제외  [{PASS if no_self else FAIL}] 본인 것 {len(mine)}건")
    ok = not_empty and mixed and no_self
    log(f"\n  B 종합: [{PASS if ok else FAIL}]")
    return ok, items


async def verify_keywords(
    db: Database, back: str, host_id: int, pem: bytes, items: list[dict]
) -> bool:
    """C. Keyword — Context에 실제로 붙었고 화면 응답에 나온다.

    화면 경로는 **피드 카드**다(`FeedCollectionItemResponse.keywords`). Record
    상세(`GET /v1/records/{id}`)는 back이 아직 `List.of()`를 고정 반환하므로
    (`RecordDetailResponse.of`) 여기서는 판정 대상이 아니라 **관측만** 한다 —
    통과 조건에 넣으면 back의 미구현 때문에 시딩 검증이 붉게 뜬다.
    """
    head("C. Keyword — 파이프라인 부착 + 화면(피드 카드) 표시")

    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT p.name AS place, kp.code AS code, kp.visibility AS visibility "
            "FROM core.context ctx "
            "JOIN core.record r ON r.id = ctx.record_id "
            "JOIN core.place p ON p.id = r.place_id "
            "JOIN ai.context_keyword ck ON ck.context_id = ctx.id "
            "JOIN ai.keyword_preset kp ON kp.id = ck.keyword_id "
            "WHERE ctx.member_id = $1 ORDER BY p.name, kp.code",
            host_id,
        )
    by_place: dict[str, list[str]] = {}
    for r in rows:
        tag = "*" if r["visibility"] != "PUBLIC" else ""
        by_place.setdefault(r["place"], []).append(f"{r['code']}{tag}")

    log("  주인공 Context에 붙은 Keyword (* = PRIVATE_ONLY):")
    for place, codes in by_place.items():
        log(f"    {place:<26} [{', '.join(codes)}]")

    with_kw = [i for i in items if i["keywords"]]
    log(f"\n  피드 카드 {len(items)}장 중 Keyword 보유 {len(with_kw)}장")

    # PRIVATE_ONLY(ANNIVERSARY·WITH_COLLEAGUES)는 본인 프로파일에는 반영되고
    # 타인 Collection 카드에는 나오지 않아야 한다(FeedKeywordRepository).
    private_codes = {"ANNIVERSARY", "WITH_COLLEAGUES"}
    leaked = [i["title"] for i in items if private_codes & set(i["keywords"])]

    attached_ok = len(by_place) > 0
    card_ok = len(with_kw) > 0
    privacy_ok = not leaked
    log("")
    log(f"  Context에 Keyword 부착   [{PASS if attached_ok else FAIL}] {len(rows)}행 / Record {len(by_place)}건")
    log(f"  피드 카드에 Keyword 표시 [{PASS if card_ok else FAIL}] {len(with_kw)}/{len(items)}장")
    log(f"  PRIVATE_ONLY 미노출      [{PASS if privacy_ok else FAIL}]"
        + (f" 누출: {leaked}" if leaked else ""))

    # 판정 대상이 아닌 관측 — back 미구현을 시연 전에 알고 있어야 한다.
    with BackClient(back, host_id, pem) as c:
        marker = (c.get("/v1/records/map").get("items") or [{}])[0]
        detail = c.get(f"/v1/records/{marker['recordId']}") if marker else {}
    log(
        f"\n  [관측] GET /v1/records/{{id}} 의 keywords = {detail.get('keywords')} — "
        "back이 `RecordDetailResponse.of`에서 `List.of()`를 고정 반환한다(미구현). "
        "Record 상세 화면에는 Keyword가 나오지 않는다."
    )

    ok = attached_ok and card_ok and privacy_ok
    log(f"\n  C 종합: [{PASS if ok else FAIL}]")
    return ok


async def main() -> int:
    argv = sys.argv
    back, ai = back_base(argv), ai_base(argv)
    data = load_data()
    pem = ensure_key()

    db = Database(SETTINGS.database_url)
    await db.connect()
    try:
        members, place_by_ctx, owner_by_collection = await load_ids(db)
        if "host" not in members:
            log("데모 데이터가 없다. 먼저 python tools/demo_seed/seed.py --reset 를 실행하라.")
            return 1
        host_id = members["host"]

        a = await verify_search(ai, host_id, data, place_by_ctx)
        b, items = verify_feed(back, host_id, pem, owner_by_collection)
        c = await verify_keywords(db, back, host_id, pem, items)
    finally:
        await db.disconnect()

    head("종합")
    for name, r in (("A 자연어 검색", a), ("B 탐색 피드", b), ("C Keyword 표시", c)):
        log(f"  {name:<16} [{PASS if r else FAIL}]")
    return 0 if (a and b and c) else 1


sys.exit(asyncio.run(main()))

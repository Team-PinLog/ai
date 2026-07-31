"""검색 결과 컷 격자용 유사도 행렬을 한 번 떠서 JSON 으로 굳힌다.

**격자 스윕이 GMS 를 다시 부르지 않게 하는 것이 이 파일의 목적이다.**

컷(`τ_abs` · `r`)은 검색 결과를 **자르기만** 하고 벡터에는 걸리지 않는다. 질의 벡터도
Context 벡터도 컷과 무관하게 고정이므로, 질의별 Record 전량의 유사도를 한 번 떠 두면
임의의 `(limit, τ_abs, r)` 조합에 대한 결과를 **임베딩 재호출 없이** 재구성할 수 있다.
`tools/tau_grid/matrix.py` 가 τ 에 대해 한 것과 같은 구조이며, 저쪽은 대상이
`(Context, Preset)` 이고 이쪽은 `(질의, Record)` 다.

유사도 계산은 `app.repository.context_embedding_repo.search` 를 **그대로 부른다.**
같은 Query 를 여기 다시 적으면 둘이 갈라지고, 갈라진 채 재면 우리가 고른 컷이 서버가
쓰는 컷이 아니게 된다. `limit` 만 크게 줘서 잘리지 않은 전량을 받는다.

    python tools/search_cut/matrix.py            # .search/matrix.json 생성
    python tools/search_cut/matrix.py --out X    # 경로 지정

GMS 임베딩 호출은 **배치 1회**다(질의 12건이 `_BATCH=128` 안에 들어간다).

행렬에 담지 않는 것: **Context 본문**. `tools/tau_grid` 의 `matrix.json` 이 본문을
전문으로 담아 gitignore 대상이 된 것과 달리, 이 파일은 Record 대표 이름(장소명)까지만
담아 커밋한다 — 다시 뜨려면 GMS 를 부르므로 유실되면 안 된다. 장소명은
`tools/demo_seed/demo_data.yaml` 이 이미 커밋하고 있는 수준이다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from app.client.embedding_client import EmbeddingClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repository import context_embedding_repo  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    # T28. 콘솔이 cp949 면 장소명 한 글자에 측정이 죽는다.
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


# 시연 정본은 15432 다. `ai/.env` 의 기본값은 07-27 잔재인 5433 이라 그대로 두면
# **데이터가 없는 DB 를 재고 「컷이 아무것도 자르지 않는다」를 결론으로 낸다**(T33).
EXPECT_PORT = "15432"

# 잘리지 않은 전량을 받기 위한 값. 실제 상한은 member 당 Record 수(최대 17)다.
NO_LIMIT = 10_000

DEMO_PROVIDER = "demo-seed"
DATA_YAML = ROOT / "tools" / "demo_seed" / "demo_data.yaml"

# **정답이 아예 없는 질의.** `demo_data.yaml` 의 12건은 전부 기대 정답을 가지므로 그것만
# 재면 `τ_abs` 의 고유 가치가 드러나지 않는다 — `r` 은 top-1 을 언제나 남기는 컷이라
# 「이 사용자에게 관련 기록이 하나도 없다」를 표현할 수 없고, 그것을 할 수 있는 것은
# 절대 하한뿐이다. 그 차이를 재려면 정답이 없는 질의가 있어야 한다.
#
# 첫 항목은 `personal-search.md §6` 이 이미 실측한 그 질의다(top-1 0.3143). 나머지 넷은
# 같은 성격 — PinLog 가 담는 것(음식점·카페·공원 방문 기록)과 범주가 다른 생활 검색이다.
# 소유자마다 보유 Record 가 다르므로 **세 소유자 각각에게** 던진다. 질의 임베딩은
# 소유자와 무관하므로 임베딩은 질의 수만큼만 든다.
OFFTOPIC_QUERIES = (
    "자동차 엔진오일 교환 정비소",
    "치과 임플란트 상담 받을 곳",
    "겨울 스키장 리프트권 파는 데",
    "노트북 액정 수리 서비스센터",
    "강아지 예방접종 동물병원",
)
OFFTOPIC_OWNERS = ("host", "jeongheon", "gahyeon")

_MEMBERS = """
SELECT provider_user_id, member_id FROM core.social_account WHERE provider = $1
"""

_RECORDS = """
SELECT r.id AS record_id, p.name AS name, c.member_id AS member_id
FROM core.record r
JOIN core.place p ON p.id = r.place_id
JOIN core.context c ON c.record_id = r.id
WHERE c.deleted_at IS NULL
GROUP BY r.id, p.name, c.member_id
"""


async def build(db: Database, settings) -> dict:
    data = yaml.safe_load(DATA_YAML.read_text(encoding="utf-8"))
    queries = data["demo_queries"]

    # 기대 정답은 `demo_data.yaml` 의 record key → place name 으로 잇는다.
    # `verify.py` 와 같은 방식이다 — seed.py 가 만든 record_id 를 파일로 넘기지 않으므로
    # 장소명이 둘을 잇는 유일한 키다.
    expect_place = {
        rec["key"]: rec["place"]["name"] for m in data["members"] for rec in m["records"]
    }

    async with db.acquire() as conn:
        members = {r["provider_user_id"]: r["member_id"] for r in await conn.fetch(_MEMBERS, DEMO_PROVIDER)}
        rec_rows = await conn.fetch(_RECORDS)
    name_by_record = {r["record_id"]: r["name"] for r in rec_rows}
    owned = {}
    for r in rec_rows:
        owned.setdefault(r["member_id"], 0)
        owned[r["member_id"]] += 1

    if "host" not in members:
        raise SystemExit("데모 데이터가 없다. 재지 않고 멈춘다.")
    log(f"  member {len(members)}명 · Record {len(name_by_record)}건 · 질의 {len(queries)}건")

    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    # 배치 1회로 부른다. 건별로 부르면 호출 수만 늘고 재는 값은 같다.
    texts = [q["query"] for q in queries] + list(OFFTOPIC_QUERIES)
    log(f"  GMS 임베딩 배치 1회 (검증 {len(queries)}건 + 무관 {len(OFFTOPIC_QUERIES)}건) …")
    embedded = await client.embed(texts)
    vectors, off_vectors = embedded[: len(queries)], embedded[len(queries) :]

    out_queries = []
    async with db.acquire() as conn:
        for q, vec in zip(queries, vectors):
            who = q.get("as", "host")  # 없으면 주인공. verify.py 와 같은 규약
            if who not in members:
                raise SystemExit(f"질의 '{q['query']}' 의 member '{who}' 가 DB 에 없다. 재지 않고 멈춘다.")
            user_id = members[who]
            rows = await context_embedding_repo.search(
                conn, user_id, settings.embedding_profile, vec, NO_LIMIT
            )
            want = expect_place[q["expect"]]
            results = [
                {
                    "rank": i,
                    "record_id": r["record_id"],
                    "context_id": r["context_id"],
                    "name": name_by_record.get(r["record_id"], f"record={r['record_id']}"),
                    "sim": round(float(r["similarity"]), 6),
                    "is_expected": name_by_record.get(r["record_id"]) == want,
                }
                for i, r in enumerate(rows, 1)
            ]
            hit = next((x for x in results if x["is_expected"]), None)
            if hit is None:
                # 기대 정답이 아예 검색되지 않으면 컷 이전의 문제다. 그 상태로 컷을
                # 재면 「컷 때문에 사라졌다」와 구별되지 않으므로 표식을 남긴다.
                log(f"  [주의] '{q['query']}' 의 기대 Record 「{want}」 가 결과에 없다")
            out_queries.append(
                {
                    "query": q["query"],
                    "as": who,
                    "user_id": user_id,
                    "expect_key": q["expect"],
                    "expect_name": want,
                    "expected_rank": hit["rank"] if hit else None,
                    "expected_sim": hit["sim"] if hit else None,
                    "owned_records": owned.get(user_id, 0),
                    "results": results,
                }
            )
            mark = f"{hit['rank']}위 {hit['sim']:.4f}" if hit else "미검색"
            log(f"    {q['query'][:28]:<30} as={who:<10} 후보 {len(results):>2}건 · 정답 {mark}")

        log()
        out_offtopic = []
        for text, vec in zip(OFFTOPIC_QUERIES, off_vectors):
            for who in OFFTOPIC_OWNERS:
                rows = await context_embedding_repo.search(
                    conn, members[who], settings.embedding_profile, vec, NO_LIMIT
                )
                results = [
                    {
                        "rank": i,
                        "record_id": r["record_id"],
                        "context_id": r["context_id"],
                        "name": name_by_record.get(r["record_id"], f"record={r['record_id']}"),
                        "sim": round(float(r["similarity"]), 6),
                        # 정답이 없는 질의다. 어느 행도 기대 정답이 아니다.
                        "is_expected": False,
                    }
                    for i, r in enumerate(rows, 1)
                ]
                out_offtopic.append(
                    {
                        "query": text,
                        "as": who,
                        "user_id": members[who],
                        "owned_records": owned.get(members[who], 0),
                        "results": results,
                    }
                )
                log(f"    [무관] {text[:24]:<26} as={who:<10} 후보 {len(results):>2}건 · "
                    f"top-1 {results[0]['sim']:.4f} {results[0]['name'][:16]}")

    return {
        "profile": settings.embedding_profile,
        "model": settings.embedding_model,
        "record_count": len(name_by_record),
        "query_count": len(out_queries),
        "offtopic_count": len(out_offtopic),
        "queries": out_queries,
        "offtopic": out_offtopic,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / ".search" / "matrix.json"))
    args = ap.parse_args()

    settings = get_settings()
    if EXPECT_PORT not in settings.database_url:
        raise SystemExit(
            f"DATABASE_URL 이 :{EXPECT_PORT} 를 가리키지 않는다 — "
            f"{settings.database_url.rsplit('@', 1)[-1]}\n"
            f"시연 정본은 :{EXPECT_PORT}(pinlog-demo)다(T33). 재지 않고 멈춘다."
        )

    log(f"  profile={settings.embedding_profile}")
    db = Database(settings.database_url)
    await db.connect()
    try:
        data = await build(db, settings)
    finally:
        await db.disconnect()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n  → {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

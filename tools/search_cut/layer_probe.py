"""질의가 어느 층에서 몇 건을 잃는지 실서버와 함께 관측한다 (S15P11A705-273).

`-255`·`-266` 은 **컷 값**을 재려고 유사도 행렬을 떴다. 이 프로브가 재는 것은 다르다 —
「짧은 질의가 결과를 못 낸다」에서 **어느 층이 얼마를 걸러냈는가**이고, 그 답은 컷
하나가 아니라 층 전체를 세어야 나온다.

    ①  후보 전량      SQL 필터(user_id · is_deleted · profile · COMPLETED) 통과 행
    ②  LIMIT          `limit` 뒤
    ③  τ_abs          절대 하한(질의 길이로 갈린다)
    ④  r              1위 대비 상대 하한
    ⑤  실서버          같은 질의를 띄운 서버에 던진 응답

**⑤ 를 함께 재는 것이 요점이다.** ③④ 는 재구성이므로 「우리가 이해한 코드」를 잴 뿐이고,
서버가 다른 것을 하고 있으면 재구성만으로는 드러나지 않는다(`-213` 이 `verify_live.py`
를 둔 이유와 같다). 여기서는 재구성과 서버가 **건수까지** 같은지 본다.

기대 Record 를 주면 그것이 **어느 층에서 사라졌는지**와 그때의 값(유사도 · 순위 ·
`r×top1`)을 함께 낸다. 「여기서 탈락한다」만으로는 처방이 안 나오기 때문이다.

    # 서버를 이 브랜치 코드로 띄운 뒤
    python tools/search_cut/layer_probe.py --ai http://127.0.0.1:8003

`--no-live` 를 주면 ①~④ 만 낸다(서버 없이). GMS 는 **질의 수만큼** 부른다 — 재구성이
질의 벡터를 필요로 하고, 서버도 요청당 1회 부른다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.client.embedding_client import EmbeddingClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repository import context_embedding_repo  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


EXPECT_PORT = "15432"
NO_LIMIT = 10_000
# 공용 계약 08 §6.1 의 `size` 기본값. back 이 항상 명시해 보내는 값이고
# `SearchRequest.limit` 의 기본값과 같다 — 설정 키가 아니라 계약값이다.
LIMIT = 20
DEMO_PROVIDER = "demo-seed"
OWNER = "jeongheon"

# 질의와 기대 Record(장소명). `-255` 가 원인을 가르려고 쓴 축을 그대로 따르되, 이
# 티켓이 만든 대역(공백·짧다 / 무공백·길다)을 더했다. 기대 Record 는 **운영에서 보고된
# 것**과 `demo_data.yaml` 본문을 근거로 적는다 — 여기만은 손으로 짝짓는다. 층을 세는
# 것이 목적이라 질의 수가 적어야 표가 읽히기 때문이다.
CASES: tuple[tuple[str, str], ...] = (
    # 운영 보고 셋 (`ai#86`·`#87`·`#88`)
    ("그네", "동교어린이공원"),
    ("신한", "카츠요"),
    ("부캠", "카츠요"),
    # 2자 — 통과하는 것과 못 하는 것
    ("스팟", "동교어린이공원"),
    ("라멘", "사루카메"),
    ("우주", "힉스커피"),
    ("공원", "치킨버거 이스트사이드"),
    # 약어 짝
    ("부트캠프", "카츠요"),
    ("신한 부캠", "카츠요"),
    ("신한 부트캠프", "카츠요"),
    # B 대역 — 공백 있고 짧다
    ("그네 공원", "치킨버거 이스트사이드"),
    ("양갱 파는", "적당"),
    ("치킨 난반", "키친갈매기"),
    # C 대역 — 공백 없고 길다
    ("그네공원", "치킨버거 이스트사이드"),
    ("비건샌드위치", "플랜트 연남점"),
    ("무한도전방영된", "감나무집기사식당"),
    ("신한부트캠프", "카츠요"),
    # 전각 공백(U+3000) — `-266` 이 「재지 않았다」고 명시한 대역이다. `_is_word_query`
    # 가 `str.isspace()` 로 보므로 **문장형**(0.30)으로 간다. U+0020 짝과 나란히 두면
    # 「구분자를 넓힌 판단이 손해였는가」에 값으로 답할 수 있다.
    ("그네　공원", "치킨버거 이스트사이드"),
    ("양갱　파는", "적당"),
    ("신한　부캠", "카츠요"),
    # 문장형 대조
    ("밥 먹고 산책하면서 쉬어가는 공원", "동교어린이공원"),
    ("신한 부트캠프 친구들과 자주 먹었던 돈카츠 집", "카츠요"),
)

_MEMBER = """
SELECT member_id FROM core.social_account
WHERE provider = $1 AND provider_user_id = $2
"""

_NAMES = """
SELECT r.id AS record_id, p.name AS name
FROM core.record r JOIN core.place p ON p.id = r.place_id
"""


def is_word_query(query: str, max_chars: int) -> bool:
    """`SearchService._is_word_query` 의 재구성. 구현을 `import` 하지 않는다."""
    q = query.strip()
    return bool(q) and not any(c.isspace() for c in q) and len(q) <= max_chars


def layers(rows: list[dict], is_word: bool, s) -> dict:
    """①~④ 를 순서대로 적용하며 각 층의 잔존 건수를 센다."""
    limited = rows[: LIMIT]
    floor = (
        s.search_similarity_floor_word if is_word else s.search_similarity_floor
    )
    after_tau = [r for r in limited if r["sim"] >= floor]
    top = limited[0]["sim"] if limited else 0.0
    after_r = [r for r in after_tau if r["sim"] >= s.search_top_ratio * top]
    return {
        "candidates": rows,
        "limited": limited,
        "after_tau": after_tau,
        "after_r": after_r,
        "floor": floor,
        "rbar": s.search_top_ratio * top,
        "top": top,
    }


def where_lost(name: str, L: dict) -> tuple[str, dict | None]:
    """기대 Record 가 어느 층에서 사라졌는가."""
    def find(rows):
        for r in rows:
            if r["name"] == name:
                return r
        return None

    hit = find(L["candidates"])
    if hit is None:
        return "① 후보에 없다", None
    if find(L["limited"]) is None:
        return "② LIMIT", hit
    if find(L["after_tau"]) is None:
        return "③ τ_abs", hit
    if find(L["after_r"]) is None:
        return "④ r", hit
    return "— 통과", hit


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", default="http://127.0.0.1:8003")
    ap.add_argument("--no-live", action="store_true", help="실서버를 부르지 않는다")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    s = get_settings()
    if EXPECT_PORT not in s.database_url:
        raise SystemExit(
            f"DATABASE_URL 이 :{EXPECT_PORT} 를 가리키지 않는다 — "
            f"{s.database_url.rsplit('@', 1)[-1]}. 재지 않고 멈춘다(T33)."
        )

    db = Database(s.database_url)
    await db.connect()
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(_MEMBER, DEMO_PROVIDER, OWNER)
            if not row:
                raise SystemExit(f"데모 데이터에 '{OWNER}' 가 없다.")
            user_id = row["member_id"]
            name_by_record = {
                r["record_id"]: r["name"] for r in await conn.fetch(_NAMES)
            }

        client = EmbeddingClient(
            base_url=s.gms_base_url,
            api_key=s.gms_api_key,
            model=s.embedding_model,
            dimension=s.embedding_dimension,
        )
        queries = [q for q, _ in CASES]
        log(f"  GMS 임베딩 {len(queries)}건 (재구성용) …")
        vecs = dict(zip(queries, await client.embed(queries)))

        log(f"  소유자 {OWNER}(user_id={user_id}) · "
            f"τ_sent={s.search_similarity_floor} · τ_word={s.search_similarity_floor_word}"
            f" · r={s.search_top_ratio} · limit={LIMIT}"
            f" · 경계 {s.search_word_query_max_chars}자\n")

        out = []
        async with db.acquire() as conn:
            for query, expect in CASES:
                rows = [
                    {
                        "record_id": r["record_id"],
                        "name": name_by_record.get(r["record_id"], "?"),
                        "sim": round(float(r["similarity"]), 6),
                    }
                    for r in await context_embedding_repo.search(
                        conn, user_id, s.embedding_profile, vecs[query], NO_LIMIT
                    )
                ]
                isw = is_word_query(query, s.search_word_query_max_chars)
                L = layers(rows, isw, s)
                verdict, hit = where_lost(expect, L)
                out.append({
                    "query": query,
                    "chars": len(query),
                    "words": len(query.split()),
                    "is_word_query": isw,
                    "expect": expect,
                    "n_candidates": len(L["candidates"]),
                    "n_limited": len(L["limited"]),
                    "n_after_tau": len(L["after_tau"]),
                    "n_after_r": len(L["after_r"]),
                    "floor": L["floor"],
                    "rbar": round(L["rbar"], 6),
                    "top": round(L["top"], 6),
                    "expect_sim": hit["sim"] if hit else None,
                    "expect_rank": (
                        [r["name"] for r in rows].index(expect) + 1 if hit else None
                    ),
                    "verdict": verdict,
                })
    finally:
        await db.disconnect()

    if not args.no_live:
        async with httpx.AsyncClient(timeout=60.0) as hc:
            for rec in out:
                resp = await hc.post(
                    f"{args.ai}/internal/v1/search",
                    headers={"X-Internal-Secret": s.internal_shared_secret},
                    json={
                        "userId": user_id,
                        "query": rec["query"],
                        "limit": LIMIT,
                        "embeddingProfile": s.embedding_profile,
                    },
                )
                resp.raise_for_status()
                rec["live_count"] = len(resp.json()["results"])
                rec["live_match"] = rec["live_count"] == rec["n_after_r"]

    log("| 질의 | 자 | 어절 | 단어형 | 하한 | ① 후보 | ② LIMIT | ③ τ | ④ r | 실서버 |"
        " 기대 sim(순위) | 탈락 |")
    log("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in out:
        live = r.get("live_count")
        live_s = "—" if live is None else (
            f"{live}" + ("" if r.get("live_match") else " **불일치**")
        )
        sim = (
            f"{r['expect_sim']:.4f}({r['expect_rank']}위)"
            if r["expect_sim"] is not None else "—"
        )
        log(f"| {r['query']} | {r['chars']} | {r['words']} "
            f"| {'●' if r['is_word_query'] else '·'} | {r['floor']} "
            f"| {r['n_candidates']} | {r['n_limited']} | {r['n_after_tau']} "
            f"| {r['n_after_r']} | {live_s} | {sim} | {r['verdict']} |")

    if not args.no_live:
        bad = [r for r in out if not r.get("live_match")]
        log(f"\n실서버 대조 {len(out) - len(bad)}/{len(out)} 일치"
            + ("" if not bad else f"  **불일치 {[r['query'] for r in bad]}**"))

    lost = {}
    for r in out:
        lost[r["verdict"]] = lost.get(r["verdict"], 0) + 1
    log("\n탈락 층 집계")
    for k in sorted(lost):
        log(f"  {k:<14} {lost[k]}건")

    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"\n  → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

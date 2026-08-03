"""「본문에 있는 말로 검색해도 안 나온다」의 원인을 가르는 프로브 (S15P11A705-255).

`ai#86`(`신한` — 완전 일치) · `ai#87`(`그네` — 부분 문자열) · `ai#88`(`부캠` — 약어)이
같은 원인인지 다른 원인인지를 실측으로 가른다. 가르는 축은 셋이다.

    ① 컷이 잘랐다        sim < τ_abs, 또는 sim < r × top1     → 설정으로 끝난다
    ② 순위에서 밀렸다     컷은 통과하는데 limit 밖              → limit 문제
    ③ 임베딩이 못 잡는다   컷 전 순위 자체가 낮다                → 구조적

`matrix.py` 와 무엇이 다른가: 저쪽은 **격자를 훑기 위해** 검증 질의 12건 + 무관 질의
15건의 전량 유사도를 굳힌다. 이쪽은 **질의 표현을 바꿔 가며** 같은 Record 가 어떻게
움직이는지 본다 — 대상 질의와 비교군을 짝지어 「짧아서인가 · 약어라서인가 · 부분어라서인가」
를 가르는 것이 목적이라 질의 목록과 기대 Record 가 다르다.

**컷 전 순위와 컷 후 포함 여부를 함께 낸다.** 이 둘이 ①과 ③을 가른다 — 컷 전에도 순위가
낮으면 컷을 풀어도 안 나오므로 컷의 문제가 아니다.

    python tools/search_cut/recall_probe.py            # .search/recall_probe.json 생성
    python tools/search_cut/recall_probe.py --out X    # 경로 지정

GMS 임베딩 호출은 **배치 1회**다(질의가 `_BATCH=128` 안에 들어간다).

행렬에 담는 것은 `matrix.py` 와 같은 수준이다 — Record 대표 이름(장소명)까지이고 Context
본문은 담지 않는다. 그래서 커밋한다(다시 뜨려면 GMS 를 부른다).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

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


# 시연 정본은 15432 다(T33). `matrix.py` 와 같은 가드 — 데이터가 없는 DB 를 재면
# 「컷이 아무것도 자르지 않는다」가 결론으로 나온다.
EXPECT_PORT = "15432"

# 잘리지 않은 전량. 컷 전 순위를 보려면 서비스의 limit(20)보다 커야 한다.
NO_LIMIT = 10_000

# 서비스가 쓰는 limit 기본값(personal-search.md §6.1, 공용 계약 08). 설정이 아니라
# 요청 파라미터라 `settings` 에 없다. 재구성에서 ②(순위 밀림)를 판정하는 기준이다.
SERVICE_LIMIT = 20

DEMO_PROVIDER = "demo-seed"

# 증상이 보고된 두 본문의 소유자. 둘 다 jeongheon 의 Record 다.
OWNER = "jeongheon"

# 대상 Record 는 장소명으로 짚는다 — `matrix.py` 가 기대 정답을 잇는 방식과 같다.
# seed.py 가 record_id 를 파일로 넘기지 않으므로 장소명이 유일한 키다.
KATSUYO = "카츠요"          # "6개월 동안 신한 부트캠프 친구들과 자주 먹었던 돈카츠 집"
DONGGYO = "동교어린이공원"     # "그네팟 스팟. 밥먹고 산책하면서 여기 머물다가 …"
SARUKAME = "사루카메"        # "미슐랭 가이드 선정된 일본인 쉐프의 라멘집 …"

# 질의 설계. `group` 이 무엇을 가르려는 축인지이고 `expect` 는 **사용자가 기대한** Record 다.
#
# 대상 넷만 재면 원인이 갈리지 않는다. 셋을 가르기 위해 비교군을 짝지었다.
#
#   길이    `신한`(2자)이 안 되고 `신한 부트캠프`가 되면 짧아서다.
#           그 대조를 성립시키는 것은 **본문에 있는 2자 단어**다 — `산책`·`라멘`이
#           되면 「2자라서」가 아니다.
#   약어    `부캠`이 안 되고 `부트캠프`가 되면 약어라서다.
#   부분어   `그네`가 안 되고 `그네팟`이 되면 토큰이 갈려서다.
#   형태    단어형이 전부 안 되고 문장형이 되면 원인은 개별 어휘가 아니라
#           **질의 형태**다. 기존 검증 질의 둘을 그대로 넣어 기준점으로 삼는다.
QUERIES: tuple[dict, ...] = (
    # ── 대상. 운영에서 보고된 넷 ────────────────────────────────────────────
    {"q": "그네팟", "expect": DONGGYO, "group": "target", "issue": "87", "note": "본문에 그대로 · 나온다고 보고"},
    {"q": "그네", "expect": DONGGYO, "group": "target", "issue": "87", "note": "부분 문자열"},
    {"q": "신한", "expect": KATSUYO, "group": "target", "issue": "86", "note": "완전 일치 단어"},
    {"q": "부캠", "expect": KATSUYO, "group": "target", "issue": "88", "note": "약어"},
    # ── 길이. 본문에 있는 단어를 글자 수만 바꿔 던진다 ──────────────────────
    {"q": "산책", "expect": DONGGYO, "group": "length", "note": "2자 · 본문 A 에 단독"},
    {"q": "라멘", "expect": SARUKAME, "group": "length", "note": "2자 · 다른 Record 본문에 단독"},
    {"q": "스팟", "expect": DONGGYO, "group": "length", "note": "2자 · 본문 A 에 단독"},
    {"q": "돈카츠", "expect": KATSUYO, "group": "length", "note": "3자 · 본문 B 에 단독"},
    {"q": "친구들", "expect": KATSUYO, "group": "length", "note": "3자 · 본문 B 에 단독"},
    {"q": "아이스크림", "expect": DONGGYO, "group": "length", "note": "5자 · 본문 A 에 단독"},
    # ── 약어. `부캠` 의 대조군 ──────────────────────────────────────────────
    {"q": "부트캠프", "expect": KATSUYO, "group": "abbrev", "note": "본문 B 에 그대로"},
    {"q": "신한 부트캠프", "expect": KATSUYO, "group": "abbrev", "note": "본문 B 에 연속으로 그대로"},
    # ── 구절. 본문 조각을 그대로 던진다. 상한 기준점 ────────────────────────
    {"q": "그네팟 스팟", "expect": DONGGYO, "group": "phrase", "note": "본문 A 첫 구절 그대로"},
    {
        "q": "신한 부트캠프 친구들과 자주 먹었던 돈카츠 집",
        "expect": KATSUYO,
        "group": "phrase",
        "note": "본문 B 거의 전문",
    },
    # ── 문장. 기존 검증 질의. 되는 것이 확인된 기준점 ───────────────────────
    {"q": "돈카츠 먹으러 자주 갔던 곳", "expect": KATSUYO, "group": "sentence", "note": "demo_queries 5번"},
    {"q": "밥 먹고 산책하면서 쉬어가는 공원", "expect": DONGGYO, "group": "sentence", "note": "demo_queries 8번"},
)

def render(row: dict) -> str:
    mark = f"{row['rank']:>2}위 {row['sim']:.4f}" if row["rank"] else "   미검색"
    return (
        f"    {row['query'][:22]:<24} → {row['expect'][:10]:<12} {mark} "
        f"| top1 {row['top1_sim']:.4f} {row['top1_name'][:12]:<14} "
        f"| 컷후 {row['kept_count']:>2}건 {'포함' if row['kept'] else '빠짐'} "
        f"| {row['cause']}"
    )


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


def apply_cut(results: list[dict], floor: float, ratio: float, limit: int) -> list[dict]:
    """서비스가 하는 것을 그대로 재구성한다 — SQL `LIMIT` 이 먼저, 컷이 뒤다.

    `app.service.search_service.SearchService._cut` 을 `import` 하지 않고 다시 적었다.
    `import` 하면 구현이 명세와 달라도 둘이 함께 틀려 재구성이 「일치」한다.

    기준이 되는 top-1 은 **컷 전** 1위다. 컷 후 재계산하면 남은 것의 1위로 기준이
    옮겨가 아무것도 더 잘리지 않는다.
    """
    head = results[:limit]
    if not head:
        return head
    if floor <= 0 and ratio <= 0:
        return head
    top = head[0]["sim"]
    return [r for r in head if r["sim"] >= floor and r["sim"] >= ratio * top]


# ①과 ③을 가르는 경계. 컷을 전부 풀었을 때(τ_abs=0 · r=0) 이 순위 안에 들어오면
# 「컷만 조정하면 회복된다」로 본다.
#
# 3 을 고른 근거는 이 데이터셋의 기존 기준선이다 — `-191` 이 임베딩 4조건 전부에서
# top-3 12/12 를 확인했고 `-213` 이 「정답이 전부 3위 안에 있다」를 적었다. 임의의 값이
# 아니라 **같은 데이터에서 이미 관측된 정답 대역**이다.
RECOVER_RANK = 3


def cut_verdict(row: dict, floor: float, ratio: float, limit: int) -> str:
    """**무엇이** 잘랐나. 컷 규칙만 본다 — 회복 가능성은 `cause` 가 따로 판정한다."""
    if row["rank"] is None:
        return "미검색"
    if row["rank"] > limit:
        # 컷 이전에 SQL LIMIT 이 잘랐다. 컷 값을 바꿔도 나오지 않는다.
        return "limit 밖"
    if row["kept"]:
        return "통과"
    if row["sim"] < floor and row["sim"] < ratio * row["top1_sim"]:
        return "컷(둘 다)"
    if row["sim"] < floor:
        return "컷(τ_abs)"
    return "컷(r)"


def cause(row: dict, limit: int) -> str:
    """①②③ 중 무엇인가. **판정 규칙을 코드에 고정해 손으로 세지 않는다.**

    `cut_verdict` 하나로는 ①과 ③이 갈리지 않는다 — 「τ_abs 가 잘랐다」는 컷을 풀면
    1위로 나오는 경우와 풀어도 8위인 경우에 똑같이 붙는다. **컷 전 순위가 그 둘을
    가른다.** 그래서 이 함수는 잘린 이유가 아니라 **컷을 풀면 회복되는가**를 본다.

        ①  컷이 잘랐고 컷을 풀면 상위 `RECOVER_RANK` 안   → 설정 조정으로 끝난다
        ②  limit 이 잘랐다                              → limit · r 문제
        ③  컷을 풀어도 상위 밖                            → 구조적. 컷으로 못 푼다
    """
    if row["rank"] is None:
        return "③ 미검색"
    if row["rank"] > limit:
        return "② limit 밖"
    if row["kept"]:
        return "— 통과"
    if row["rank"] <= RECOVER_RANK:
        return f"① 컷이 잘랐다(풀면 {row['rank']}위)"
    return f"③ 컷을 풀어도 {row['rank']}위"


async def build(db: Database, settings) -> dict:
    floor = settings.search_similarity_floor
    ratio = settings.search_top_ratio

    async with db.acquire() as conn:
        members = {
            r["provider_user_id"]: r["member_id"]
            for r in await conn.fetch(_MEMBERS, DEMO_PROVIDER)
        }
        rec_rows = await conn.fetch(_RECORDS)

    if OWNER not in members:
        raise SystemExit(f"데모 데이터에 '{OWNER}' 가 없다. 재지 않고 멈춘다.")
    user_id = members[OWNER]
    name_by_record = {r["record_id"]: r["name"] for r in rec_rows}
    owned = sum(1 for r in rec_rows if r["member_id"] == user_id)

    # 대상 장소명이 실제로 있는지 먼저 본다. 없으면 재봐야 「안 나온다」만 나온다.
    have = {r["name"] for r in rec_rows if r["member_id"] == user_id}
    for want in {q["expect"] for q in QUERIES}:
        if want not in have:
            raise SystemExit(f"기대 Record 「{want}」 가 {OWNER} 에게 없다. 재지 않고 멈춘다.")

    log(f"  {OWNER} member_id={user_id} · 보유 Record {owned}건 · 질의 {len(QUERIES)}건")
    log(f"  컷 τ_abs={floor} · r={ratio} · limit={SERVICE_LIMIT}\n")

    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    log(f"  GMS 임베딩 배치 1회 ({len(QUERIES)}건) …")
    vectors = await client.embed([q["q"] for q in QUERIES])

    out = []
    async with db.acquire() as conn:
        for spec, vec in zip(QUERIES, vectors):
            rows = await context_embedding_repo.search(
                conn, user_id, settings.embedding_profile, vec, NO_LIMIT
            )
            results = [
                {
                    "rank": i,
                    "record_id": r["record_id"],
                    "name": name_by_record.get(r["record_id"], f"record={r['record_id']}"),
                    "sim": round(float(r["similarity"]), 6),
                }
                for i, r in enumerate(rows, 1)
            ]
            top1 = results[0] if results else None
            kept = apply_cut(results, floor, ratio, SERVICE_LIMIT)
            kept_ids = {r["record_id"] for r in kept}
            hit = next((r for r in results if r["name"] == spec["expect"]), None)

            row = {
                "query": spec["q"],
                "group": spec["group"],
                "issue": spec.get("issue"),
                "note": spec["note"],
                "expect": spec["expect"],
                "rank": hit["rank"] if hit else None,
                "sim": hit["sim"] if hit else None,
                "top1_name": top1["name"] if top1 else None,
                "top1_sim": top1["sim"] if top1 else None,
                "kept": bool(hit and hit["record_id"] in kept_ids),
                "kept_count": len(kept),
                "candidate_count": len(results),
                # r 컷의 기준선. 「0.30 은 넘는데 잘렸다」를 눈으로 확인하는 값이다.
                "ratio_floor": round(ratio * top1["sim"], 6) if top1 else None,
                "results": results,
                "kept_names": [r["name"] for r in kept],
            }
            row["cut_verdict"] = cut_verdict(row, floor, ratio, SERVICE_LIMIT)
            row["cause"] = cause(row, SERVICE_LIMIT)
            out.append(row)
            log(render(row))

    return {
        "ticket": "S15P11A705-255",
        "profile": settings.embedding_profile,
        "model": settings.embedding_model,
        "owner": OWNER,
        "user_id": user_id,
        "owned_records": owned,
        "cut": {"tau_abs": floor, "ratio": ratio, "limit": SERVICE_LIMIT},
        "queries": out,
    }


def replay(path: Path, settings) -> dict:
    """굳힌 행렬로 **판정만** 다시 낸다. DB 도 GMS 도 부르지 않는다.

    `-210`·`-213` 이 세운 구조를 그대로 따른다 — 벡터를 한 번 떠서 굳히고 그 위에서
    규칙을 훑는다. 컷도 판정 규칙도 유사도에 걸릴 뿐 임베딩에 걸리지 않으므로,
    규칙을 고칠 때마다 GMS 를 다시 부를 이유가 없다.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    floor = settings.search_similarity_floor
    ratio = settings.search_top_ratio
    limit = data["cut"]["limit"]
    for row in data["queries"]:
        kept = apply_cut(row["results"], floor, ratio, limit)
        kept_ids = {r["record_id"] for r in kept}
        hit = next((r for r in row["results"] if r["name"] == row["expect"]), None)
        row["kept"] = bool(hit and hit["record_id"] in kept_ids)
        row["kept_count"] = len(kept)
        row["kept_names"] = [r["name"] for r in kept]
        row["cut_verdict"] = cut_verdict(row, floor, ratio, limit)
        row["cause"] = cause(row, limit)
        log(render(row))
    data["cut"] = {"tau_abs": floor, "ratio": ratio, "limit": limit}
    return data


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / ".search" / "recall_probe.json"))
    ap.add_argument(
        "--replay",
        metavar="JSON",
        help="굳힌 행렬을 읽어 판정만 다시 낸다. DB·GMS 를 부르지 않는다",
    )
    args = ap.parse_args()

    settings = get_settings()

    if args.replay:
        src = Path(args.replay)
        log(f"  replay {src}  (GMS·DB 호출 없음)")
        log(f"  컷 τ_abs={settings.search_similarity_floor} · "
            f"r={settings.search_top_ratio}\n")
        data = replay(src, settings)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"\n  → {out}  ({out.stat().st_size:,} bytes)")
        return 0

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

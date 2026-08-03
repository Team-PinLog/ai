"""단어형 질의의 유사도 행렬을 한 번 떠서 굳힌다 (S15P11A705-266).

`matrix.py` 가 **문장형** 검증 질의 12건으로 한 것을 **단어형**으로 한다. 컷 값을 다시
정하려면 `-255` 가 쓴 22건으로는 부족하다 — 그것은 세 이슈의 원인을 가르기 위한 진단
집합이라 **정답이 있는 질의에 치우쳐 있고 무관 통제가 `치과` 하나**다.

**무관 통제가 하나면 이 티켓의 질문에 답할 수 없다.** `-213` 이 정확히 그 함정을 밟았다
— `personal-search.md §6` 이 무관 질의 **1건**으로 「간격 +0.2120, 컷 불필요」를 결론
냈고, 15건으로 늘리자 간격이 **-0.0176** 이 되어 결론이 뒤집혔다. 단어형에서 `τ_abs` 를
내리는 판단은 「무관 대역이 어디까지 올라오는가」가 전부이므로, 그 대역을 1점으로 재면
안 된다.

## 기대 정답을 손으로 정하지 않는다

`matrix.py` 는 `demo_data.yaml` 의 `expect` 를 쓰고 `-255` 는 작업자가 짝지었다. 단어형은
**질의 하나에 정답이 여럿**이라(`라멘` → 사루카메·쿠로코, `신한` → 6건) 손으로 정하면
판정자의 재량이 결과를 만든다.

그래서 기대 집합을 **문자열 포함으로 기계 계산한다.**

    expect(질의, 소유자) = { 그 소유자의 Record 중 본문에 질의가 그대로 있는 것 }

이것이 이 티켓의 문제 정의와 정확히 일치한다 — `ai#87` 의 요구가 「본문에 있는 말로
검색하면 그 기록이 나와야 한다」이기 때문이다. `-255` 가 **완전 일치는 유사도에 유의미하게
기여하지 않는다**를 실측했지만, 그것은 「완전 일치가 정답 기준이 아니다」가 아니라
**「임베딩이 그 기준을 못 따라간다」**는 뜻이다. 재는 쪽의 기준은 사용자 기대에 둔다.

부작용이 이득이다 — **같은 질의를 소유자 셋에게 던지면 정답 있는 행과 없는 행이 동시에
생긴다.** 후자가 통제가 되므로 표본이 공짜로 는다.

## 통제를 두 층으로 나눈다

「정답이 없다」가 「무관하다」는 아니다. `라멘` 을 host 에게 던지면 host 본문에 라멘은
없지만 host 는 음식점 기록을 갖고 있다. `-213` 의 무관 질의는 **범주 자체가 다른 것**
(엔진오일·치과)이었고 그 엄격함을 잃으면 「무관 통과」가 물러진다.

    word       그 소유자 본문에 질의가 있다          정답 누락을 센다
    cross      같은 단어형인데 그 소유자에겐 없다     본문에 없는데 통과하는가
    offtopic   PinLog 범주 밖 생활 검색             `-213` 무관 질의 15건의 단어형 대응

`cross` 는 `offtopic` 보다 무르다(같은 생활권 어휘라 우연히 겹칠 수 있다). 그래서 표에
합치지 않고 따로 낸다.

    python tools/search_cut/word_matrix.py          # .search/word_grid.json 생성

GMS 임베딩 호출은 **배치 1회**다(질의가 `_BATCH=128` 안에 들어간다). 소유자별 검색은
질의 벡터를 재사용하므로 임베딩이 늘지 않는다.

행렬에 담는 것은 `matrix.py`·`recall_probe.py` 와 같은 수준이다 — Record 대표
이름(장소명)까지이고 **Context 본문은 담지 않는다.** 그래서 커밋한다.
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


# 시연 정본은 15432 다(T33). `matrix.py`·`recall_probe.py` 와 같은 가드 — 데이터가 없는
# DB 를 재면 「컷이 아무것도 자르지 않는다」가 결론으로 나온다.
EXPECT_PORT = "15432"

# 잘리지 않은 전량. 컷 전 순위를 보려면 서비스의 limit(20)보다 커야 한다.
NO_LIMIT = 10_000

DEMO_PROVIDER = "demo-seed"

# 소유자 셋. `-213` 의 무관 질의가 쓴 것과 같은 셋이라 문장형 표와 나란히 읽힌다.
# 보유 Record 가 6·11·17 로 갈려 **기록 수에 따른 편차**(`-213` §무관 질의도 소유자에
# 따라 갈린다)가 이 표에서도 보인다.
OWNERS = ("host", "gahyeon", "jeongheon")

# ── 단어형 질의. 본문에서 뽑았고 **어느 소유자에게 정답인지는 계산이 정한다** ──────
#
# 길이를 고루 깔았다. `-255` 가 「3자 이상은 전부 ≥0.3189, 2자는 전부 ≤0.2818」을 봤고
# 그 경계에 현행 `τ_abs=0.30` 이 있으므로, 2자를 두껍게 깔아야 교환점이 보인다.
#
# 운영 보고 셋(`그네`·`신한`·`부캠`)을 맨 앞에 둔다 — 이 티켓이 회복시키려는 대상이다.
WORD_QUERIES: tuple[str, ...] = (
    # 운영에서 보고된 것
    "그네", "신한", "부캠",
    # 2자 — 본문에 그대로 있는 것
    "스팟", "산책", "라멘", "피맥", "비건", "서점", "양갱", "우주", "소파",
    "보쌈", "난반", "연어", "야경", "우산", "수다", "국밥", "공원", "맛집",
    "두부", "언덕", "카페", "만두", "초밥", "피자",
    # 3자
    "그네팟", "돈카츠", "미슐랭", "차슈밥", "칼국수", "아부라", "브런치", "디저트",
    "노트북", "콘센트", "기념일", "부모님", "빗소리", "6개월", "감자전", "일식집",
    # 4자 이상
    "부트캠프", "두부찌개", "무한도전", "치킨버거", "샌드위치", "화덕피자", "낙지전골",
    "가지튀김", "라따뚜이", "돼지국밥", "인테리어", "아이스크림",
)

# ── 무관 통제. **어느 소유자의 어느 본문에도 없어야 한다** ────────────────────────
#
# `-213` 의 무관 질의 5종(자동차 엔진오일 교환 정비소 · 치과 임플란트 상담 · 겨울 스키장
# 리프트권 · 노트북 액정 수리 · 강아지 예방접종)을 **단어형으로 쪼갠 것**이 뼈대다. 문장형
# 표와 같은 범주를 재야 「길이만 다른가」를 물을 수 있다.
#
# `노트북` 은 여기 넣지 않는다 — host 본문에 그대로 있다(「혼자 노트북 들고 와서」).
# 「노트북 액정 수리」의 무관 성분은 `액정` 이다. 이런 실수를 사람이 알아채지 못하므로
# **본문 대조 가드가 아래에서 전수 확인하고, 걸리면 재지 않고 멈춘다.**
OFFTOPIC_QUERIES: tuple[str, ...] = (
    # 2자
    "치과", "액정", "약국", "보험", "세탁",
    # 3자
    "스키장", "정비소", "리프트", "헬스장", "주유소", "안경점",
    # 4자
    "엔진오일", "임플란트", "예방접종", "동물병원",
)

_MEMBERS = """
SELECT provider_user_id, member_id FROM core.social_account WHERE provider = $1
"""

# 기대 집합을 계산하려면 본문이 필요하다. **본문은 행렬에 담지 않고** 여기서 포함 여부만
# 판정해 `record_id` 목록으로 바꾼다 — `recall_probe.py --lengths` 가 본문 길이를 DB 에서
# 읽는 것과 같은 원칙이다.
_RECORDS = """
SELECT r.id AS record_id, p.name AS name, c.member_id AS member_id, c.body AS body
FROM core.record r
JOIN core.place p ON p.id = r.place_id
JOIN core.context c ON c.record_id = r.id
WHERE c.deleted_at IS NULL
"""


def expected_ids(word: str, rows: list, user_id: int) -> set[int]:
    """그 소유자의 Record 중 **본문에 질의가 그대로 있는 것**.

    장소명은 보지 않는다. 임베딩이 받는 것은 `context` 하나뿐이므로(`demo_data.yaml` §①)
    장소명을 정답 기준에 넣으면 **재는 쪽이 모델에 없는 정보를 기대**하게 된다.
    「진우네 초밥」의 본문에 「초밥」이 없는 것이 그 예다.
    """
    return {
        r["record_id"]
        for r in rows
        if r["member_id"] == user_id and word in r["body"]
    }


async def build(db: Database, settings) -> dict:
    async with db.acquire() as conn:
        members = {
            r["provider_user_id"]: r["member_id"]
            for r in await conn.fetch(_MEMBERS, DEMO_PROVIDER)
        }
        rec_rows = list(await conn.fetch(_RECORDS))

    for who in OWNERS:
        if who not in members:
            raise SystemExit(f"데모 데이터에 '{who}' 가 없다. 재지 않고 멈춘다.")
    name_by_record = {r["record_id"]: r["name"] for r in rec_rows}
    owned = {}
    for r in rec_rows:
        owned[r["member_id"]] = owned.get(r["member_id"], 0) + 1

    # 가드 ①. 무관 통제가 실제로 무관한지 **전수 대조한다.** 하나라도 본문에 있으면
    # 그 행은 통제가 아니라 정답 있는 질의이고, 섞인 채 재면 「무관 통과」가 과소평가된다.
    bad = [
        (w, name_by_record[r["record_id"]])
        for w in OFFTOPIC_QUERIES
        for r in rec_rows
        if w in r["body"]
    ]
    if bad:
        lines = "\n".join(f"    「{w}」 가 「{n}」 본문에 있다" for w, n in bad)
        raise SystemExit(f"무관 통제 질의가 본문에 있다. 재지 않고 멈춘다.\n{lines}")

    # 가드 ②. 단어형 질의가 **어느 소유자에게도** 정답이 없으면 설계 오류다. 그대로 재면
    # 정답 누락 분모에서 조용히 빠져 컷이 실제보다 안전해 보인다.
    orphan = [
        w
        for w in WORD_QUERIES
        if not any(expected_ids(w, rec_rows, members[o]) for o in OWNERS)
    ]
    if orphan:
        raise SystemExit(
            f"단어형 질의 {len(orphan)}건이 어느 소유자 본문에도 없다: {orphan}\n"
            "무관 통제로 옮기거나 질의를 고쳐라. 재지 않고 멈춘다."
        )

    log(f"  소유자 {len(OWNERS)}명 · Record {len(rec_rows)}건 "
        f"· 단어형 {len(WORD_QUERIES)}건 · 무관 {len(OFFTOPIC_QUERIES)}건")
    log(f"  현행 컷 τ_abs={settings.search_similarity_floor} "
        f"· r={settings.search_top_ratio}\n")

    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    texts = list(WORD_QUERIES) + list(OFFTOPIC_QUERIES)
    log(f"  GMS 임베딩 배치 1회 ({len(texts)}건) …")
    embedded = await client.embed(texts)
    vec_by_text = dict(zip(texts, embedded))

    out_word, out_cross, out_off = [], [], []
    async with db.acquire() as conn:
        for word in WORD_QUERIES:
            for who in OWNERS:
                user_id = members[who]
                want = expected_ids(word, rec_rows, user_id)
                rows = await context_embedding_repo.search(
                    conn, user_id, settings.embedding_profile,
                    vec_by_text[word], NO_LIMIT,
                )
                results = [
                    {
                        "rank": i,
                        "record_id": r["record_id"],
                        "name": name_by_record.get(r["record_id"], f"record={r['record_id']}"),
                        "sim": round(float(r["similarity"]), 6),
                        "is_expected": r["record_id"] in want,
                    }
                    for i, r in enumerate(rows, 1)
                ]
                entry = {
                    "query": word,
                    "chars": len(word),
                    "as": who,
                    "user_id": user_id,
                    "owned_records": owned.get(user_id, 0),
                    "expect_count": len(want),
                    "expect_names": sorted(name_by_record[i] for i in want),
                    "results": results,
                }
                (out_word if want else out_cross).append(entry)

        for word in OFFTOPIC_QUERIES:
            for who in OWNERS:
                user_id = members[who]
                rows = await context_embedding_repo.search(
                    conn, user_id, settings.embedding_profile,
                    vec_by_text[word], NO_LIMIT,
                )
                out_off.append({
                    "query": word,
                    "chars": len(word),
                    "as": who,
                    "user_id": user_id,
                    "owned_records": owned.get(user_id, 0),
                    "expect_count": 0,
                    "expect_names": [],
                    "results": [
                        {
                            "rank": i,
                            "record_id": r["record_id"],
                            "name": name_by_record.get(r["record_id"], f"record={r['record_id']}"),
                            "sim": round(float(r["similarity"]), 6),
                            "is_expected": False,
                        }
                        for i, r in enumerate(rows, 1)
                    ],
                })

    for label, rows_ in (("정답 있음", out_word), ("교차 통제", out_cross), ("무관 통제", out_off)):
        log(f"  {label:<8} {len(rows_):>3}행")
    log()
    # 대역을 바로 찍는다. 이 티켓의 결론은 **두 대역이 겹치는가 역전인가**가 지배하므로
    # 격자보다 먼저 눈에 보여야 한다(`-213` 이 분포를 격자 앞에 둔 것과 같은 이유).
    hits = [x["sim"] for q in out_word for x in q["results"] if x["is_expected"]]
    off1 = [q["results"][0]["sim"] for q in out_off if q["results"]]
    if hits and off1:
        log(f"  정답 최솟값 {min(hits):.4f}  ·  중앙값 {sorted(hits)[len(hits) // 2]:.4f}")
        log(f"  무관 top-1 최댓값 {max(off1):.4f}")
        log(f"  → 간격 {min(hits) - max(off1):+.4f}"
            + ("   역전. 단일 τ_abs 로 가를 수 없다" if min(hits) < max(off1) else ""))

    return {
        "ticket": "S15P11A705-266",
        "profile": settings.embedding_profile,
        "model": settings.embedding_model,
        "owners": list(OWNERS),
        "record_count": len(rec_rows),
        "cut": {
            "tau_abs": settings.search_similarity_floor,
            "ratio": settings.search_top_ratio,
        },
        "word_count": len(out_word),
        "cross_count": len(out_cross),
        "offtopic_count": len(out_off),
        "queries": out_word,
        "cross": out_cross,
        "offtopic": out_off,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / ".search" / "word_grid.json"))
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

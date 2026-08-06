"""단어형 판정 경계의 두 정의를 가르는 행렬 (S15P11A705-273).

`-266` 이 `_is_word_query` 를 **공백 없음 AND ≤5자** 두 조건의 논리곱으로 두면서 스스로
적었다 — *「측정한 단어형이 전부 공백 없는 2~5자이고 문장형이 전부 공백 포함 6자↑라
「글자 수」와 「어절 수」 두 정의가 같은 답을 냈다. 둘이 갈리는 질의가 이 행렬에 없다」*
(리포트 §말할 수 없는 것). **이 스크립트가 그 대역을 만든다.**

## 두 정의가 갈리는 자리

    현행 규칙   공백 없음 AND 글자 수 ≤ SEARCH_WORD_QUERY_MAX_CHARS(5)  → τ_abs=0.24
                그 밖                                                  → τ_abs=0.30

    A  공백 없고 짧다   `그네`             양쪽 정의가 단어형   ← -266 이 잰 대역
    B  공백 있고 짧다   `그네 공원`(4자)   글자 수는 단어형 · 어절 수는 문장형   ← 미측정
    C  공백 없고 길다   `비건샌드위치`(6자) 글자 수는 문장형 · 어절 수는 단어형   ← 미측정
    D  공백 있고 길다   문장형 질의        양쪽 정의가 문장형   ← -213 이 잰 대역

**B 와 C 가 이 티켓의 대상이다.** 현행은 둘 다 문장형(0.30)으로 보내는데, 그 선택이
안전 방향으로의 판단이었을 뿐 측정된 적이 없다.

## 짝을 만들어 공백만 바꾼다

본문의 **문장 내 인접 어절쌍**을 뽑아 두 형태로 낸다.

    pair (그네, 공원)  →  spaced  "그네 공원"   (5자 · 2어절)
                          joined  "그네공원"    (4자 · 1어절)

두 질의의 **기대 정답 집합이 같다** — 같은 쌍에서 나왔으므로. 그래서 유사도 차이와 판정
차이가 전부 「공백 하나」에 귀속된다. 축이 하나만 움직이는 짝이 없으면 「글자 수인가 어절
수인가」는 답이 나오지 않는다.

## 질의를 고르지 않는다 — 전량이다

`-266` 은 한계로 이렇게 적었다 — *「단어형 질의 54건은 이 티켓의 작업자가 본문에서
뽑았다. 「본문에 있는 말」이라는 기준은 기계적이지만 **어떤 말을 뽑을지**는 재량이었다」*.

여기서는 조건에 맞는 쌍을 **전부** 잰다. 선정이 없으므로 재량도 없다.

    조건   같은 문장 안에서 인접 · 두 어절 모두 2자 이상

**남는 재량은 「2자 이상」 하나다.** 1자 어절은 조사·의존명사·수식어 비중이 높아
(`이 거`·`한 잔`·`다 같이`) 질의로 성립하지 않는 쌍을 대량으로 만든다. 이것이 이 측정에서
사람이 내린 유일한 판단이고, 값을 바꾸려면 `MIN_TOKEN_CHARS` 하나만 바꾸면 된다.

## 기대 정답 — `-266` 기준의 자연스러운 확장

    expect(쌍, 소유자) = { 그 소유자의 Record 중 **그 쌍이 문장 안에서 인접**한 것 }

`-266` 은 1어절이라 「본문에 그 문자열이 있다」로 충분했다. 2어절에서 그 기준을 그대로
쓰면 `joined` 형태(`그네공원`)가 본문에 없어 **정답이 0이 된다** — 사용자가 붙여 쓴 것을
「기대 없음」으로 세는 것은 재는 쪽의 오류다. 쌍의 출처가 본문이므로 두 형태 모두 같은
Record 를 기대한다.

## 무관 통제

`-213`·`-266` 의 무관 질의 5종을 **쌍으로** 만든 것이다. 통제도 `spaced`/`joined` 짝을
가져야 「공백을 바꾸면 무관 통과가 어떻게 변하는가」에 답할 수 있다. 어느 본문에도 없는지
전수 대조하고, 하나라도 걸리면 GMS 를 부르기 전에 멈춘다(`-266` 가드 ①과 같다).

    python tools/search_cut/boundary_matrix.py            # .search/boundary_grid.json
    python tools/search_cut/boundary_matrix.py --dry      # 대역 분포와 질의만. **GMS 미호출**
                                                          # (DB 는 읽는다 — 쌍이 본문에서 온다)

행렬에 담는 것은 `word_grid.json` 과 같은 수준이다 — Record 대표 이름(장소명)까지이고
**Context 본문은 담지 않는다.** 그래서 커밋한다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
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


# 시연 정본은 15432 다(T33). `word_matrix.py` 와 같은 가드.
EXPECT_PORT = "15432"

# 잘리지 않은 전량. 컷 전 순위를 보려면 서비스의 limit(20)보다 커야 한다.
NO_LIMIT = 10_000

DEMO_PROVIDER = "demo-seed"

# `word_matrix.py` 와 같은 셋. 보유 Record 6·11·17 로 갈려 기록 수 편차가 보인다.
OWNERS = ("host", "gahyeon", "jeongheon")

# 이 측정의 유일한 재량(§질의를 고르지 않는다).
MIN_TOKEN_CHARS = 2

# 문장 경계. 이것을 넘는 인접쌍(`가게 낡은`·`맛집 예전에`)은 질의로 성립하지 않는다.
_SENT = re.compile(r"[.!?…]+")
# 어절 분리자. 괄호·쉼표는 지우지 않고 **띄운다** — `구운연어덮밥(구연덮)이` 를 지우면
# `구운연어덮밥구연덮이` 라는 없는 말이 생긴다.
_SEP = re.compile(r"[(),·:;\"'\[\]~/]")

# ── 무관 통제. `-213` 무관 질의 5종(자동차 엔진오일 교환 정비소 · 치과 임플란트 상담 ·
#    겨울 스키장 리프트권 · 노트북 액정 수리 · 강아지 예방접종)의 성분으로 만든 쌍이다.
#    길이를 4~8자에 고루 깔아 정답 쌍의 대역과 겹치게 둔다.
#    `노트북` 은 host 본문에 있어(「혼자 노트북 들고 와서」) 쓰지 않는다 — 아래 가드가
#    사람의 착각과 무관하게 전수 대조한다.
OFFTOPIC_PAIRS: tuple[tuple[str, str], ...] = (
    ("액정", "수리"),
    ("치과", "보험"),
    ("약국", "세탁"),
    ("주유소", "세탁"),
    ("안경점", "보험"),
    ("헬스장", "보험"),
    ("스키장", "리프트"),
    ("엔진오일", "교환"),
    ("치과", "임플란트"),
    ("강아지", "예방접종"),
    ("정비소", "엔진오일"),
    ("동물병원", "예방접종"),
)

_MEMBERS = """
SELECT provider_user_id, member_id FROM core.social_account WHERE provider = $1
"""

_RECORDS = """
SELECT r.id AS record_id, p.name AS name, c.member_id AS member_id, c.body AS body
FROM core.record r
JOIN core.place p ON p.id = r.place_id
JOIN core.context c ON c.record_id = r.id
WHERE c.deleted_at IS NULL
"""


def tokenize(body: str) -> list[list[str]]:
    """본문을 문장별 어절 목록으로. 문장 경계를 살린다."""
    out = []
    for sent in _SENT.split(_SEP.sub(" ", body)):
        toks = [t for t in sent.split() if t]
        if toks:
            out.append(toks)
    return out


def adjacent_pairs(body: str) -> set[tuple[str, str]]:
    """문장 안에서 인접한 어절쌍. 두 어절 모두 `MIN_TOKEN_CHARS` 자 이상."""
    return {
        (a, b)
        for sent in tokenize(body)
        for a, b in zip(sent, sent[1:])
        if len(a) >= MIN_TOKEN_CHARS and len(b) >= MIN_TOKEN_CHARS
    }


def forms(pair: tuple[str, str]) -> list[tuple[str, str]]:
    """쌍 → (형태 이름, 질의). 공백 하나만 다르다."""
    a, b = pair
    return [("spaced", f"{a} {b}"), ("joined", f"{a}{b}")]


def expected_ids(pair: tuple[str, str], rows: list, user_id: int) -> set[int]:
    """그 소유자의 Record 중 그 쌍이 **문장 안에서 인접**한 것.

    `spaced`·`joined` 가 같은 집합을 받는다 — 쌍이 같기 때문이다. 이것이 짝 대조의
    전제다(§기대 정답).
    """
    return {
        r["record_id"]
        for r in rows
        if r["member_id"] == user_id and pair in adjacent_pairs(r["body"])
    }


def entry_shape(query: str, form: str, pair: tuple[str, str]) -> dict:
    """질의의 형태 지표. 두 정의가 무엇을 보는지 여기서 갈린다."""
    q = query.strip()
    return {
        "query": query,
        "form": form,
        "pair": list(pair),
        # 현행 `_is_word_query` 가 보는 값 — 원문 그대로의 글자 수
        "chars": len(q),
        # 공백을 뗀 글자 수. 「글자 수」 정의를 공백 제외로 읽으면 이쪽이다
        "chars_nospace": len("".join(q.split())),
        "words": len(q.split()),
        "has_space": any(c.isspace() for c in q),
    }


async def build(db: Database, settings, dry: bool) -> dict:
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
    owned: dict[int, int] = {}
    for r in rec_rows:
        owned[r["member_id"]] = owned.get(r["member_id"], 0) + 1

    # 쌍 전량. 소유자 셋의 Record 에서만 뽑는다 — 다른 소유자 본문에서 온 쌍은 셋 중
    # 누구에게도 정답이 없어 `cross` 만 불린다.
    owner_ids = {members[o] for o in OWNERS}
    pairs = sorted(
        {
            p
            for r in rec_rows
            if r["member_id"] in owner_ids
            for p in adjacent_pairs(r["body"])
        }
    )

    # 가드 ①. 무관 통제가 실제로 무관한지 **전수 대조한다.** 어절 하나라도 어느 본문에
    # 있으면 그 행은 통제가 아니다. `joined` 형태도 본문 문자열로 대조한다.
    bad = []
    for a, b in OFFTOPIC_PAIRS:
        for r in rec_rows:
            for tok in (a, b, f"{a}{b}"):
                if tok in r["body"]:
                    bad.append((f"{a} {b}", tok, name_by_record[r["record_id"]]))
    if bad:
        lines = "\n".join(f"    「{p}」 의 「{t}」 가 「{n}」 본문에 있다" for p, t, n in bad)
        raise SystemExit(f"무관 통제가 본문에 있다. 재지 않고 멈춘다.\n{lines}")

    # 가드 ②. 쌍은 본문에서 왔으므로 소유자 셋 중 하나에는 반드시 정답이 있다. 없으면
    # 추출과 판정이 어긋난 것이고, 그대로 재면 정답 누락 분모에서 조용히 빠진다.
    orphan = [
        p for p in pairs if not any(expected_ids(p, rec_rows, members[o]) for o in OWNERS)
    ]
    if orphan:
        raise SystemExit(
            f"쌍 {len(orphan)}건이 어느 소유자 본문에도 없다: {orphan[:10]}\n"
            "추출과 판정이 어긋났다. 재지 않고 멈춘다."
        )

    queries = [(q, f, p) for p in pairs for f, q in forms(p)]
    off_queries = [(q, f, p) for p in OFFTOPIC_PAIRS for f, q in forms(p)]

    log(f"  소유자 {len(OWNERS)}명 · Record {len(rec_rows)}건")
    log(f"  쌍 {len(pairs)}종 × 2형태 = 질의 {len(queries)}건 "
        f"· 무관 쌍 {len(OFFTOPIC_PAIRS)}종 × 2 = {len(off_queries)}건")
    log(f"  현행 컷 τ_abs={settings.search_similarity_floor}"
        f" · τ_word={settings.search_similarity_floor_word}"
        f" · r={settings.search_top_ratio}"
        f" · 경계 {settings.search_word_query_max_chars}자")

    # 현행 규칙이 이 질의들을 어떻게 가르는지 먼저 찍는다. GMS 를 부르기 전에 「대역이
    # 실제로 생겼는가」가 보여야 한다.
    band = {"A": 0, "B": 0, "C": 0, "D": 0}
    for q, f, p in queries:
        s = entry_shape(q, f, p)
        short = s["chars"] <= settings.search_word_query_max_chars
        band["A" if (not s["has_space"] and short)
             else "B" if (s["has_space"] and short)
             else "C" if (not s["has_space"] and not short)
             else "D"] += 1
    log(f"  대역  A(무공백·짧다) {band['A']}  B(공백·짧다) {band['B']}  "
        f"C(무공백·길다) {band['C']}  D(공백·길다) {band['D']}\n")

    if dry:
        # 대역별로 보여준다. 전체를 사전순으로 흘리면 B·C 가 눈에 안 들어온다 —
        # 이 측정이 만든 것이 그 둘이므로 그쪽이 보여야 dry run 이 쓸모가 있다.
        by_band: dict[str, list[str]] = {"A": [], "B": [], "C": [], "D": []}
        for q, f, p in queries:
            s = entry_shape(q, f, p)
            short = s["chars"] <= settings.search_word_query_max_chars
            key = ("A" if (not s["has_space"] and short)
                   else "B" if (s["has_space"] and short)
                   else "C" if (not s["has_space"] and not short) else "D")
            by_band[key].append(f"{s['chars']:>2}자 {s['words']}어절  {q}")
        for key, label in (
            ("A", "무공백·짧다 — 현행 단어형"),
            ("B", "공백·짧다 — 현행 문장형 (글자 수 정의라면 단어형)"),
            ("C", "무공백·길다 — 현행 문장형 (어절 수 정의라면 단어형)"),
            ("D", "공백·길다 — 현행 문장형"),
        ):
            log(f"  [{key}] {label}   {len(by_band[key])}건")
            for line in by_band[key][:12]:
                log(f"      {line}")
            if len(by_band[key]) > 12:
                log(f"      … {len(by_band[key]) - 12}건 더")
            log()
        return {}

    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    texts = [q for q, _, _ in queries] + [q for q, _, _ in off_queries]
    log(f"  GMS 임베딩 {len(texts)}건 …")
    embedded = await client.embed(texts)
    vec_by_text = dict(zip(texts, embedded))

    out_word: list[dict] = []
    out_cross: list[dict] = []
    out_off: list[dict] = []

    async def rows_for(query: str, user_id: int, conn) -> list[dict]:
        rows = await context_embedding_repo.search(
            conn, user_id, settings.embedding_profile, vec_by_text[query], NO_LIMIT
        )
        return rows

    async with db.acquire() as conn:
        for query, form, pair in queries:
            for who in OWNERS:
                user_id = members[who]
                want = expected_ids(pair, rec_rows, user_id)
                rows = await rows_for(query, user_id, conn)
                entry = entry_shape(query, form, pair)
                entry.update({
                    "as": who,
                    "user_id": user_id,
                    "owned_records": owned.get(user_id, 0),
                    "expect_count": len(want),
                    "expect_names": sorted(name_by_record[i] for i in want),
                    "results": [
                        {
                            "rank": i,
                            "record_id": r["record_id"],
                            "name": name_by_record.get(
                                r["record_id"], f"record={r['record_id']}"
                            ),
                            "sim": round(float(r["similarity"]), 6),
                            "is_expected": r["record_id"] in want,
                        }
                        for i, r in enumerate(rows, 1)
                    ],
                })
                (out_word if want else out_cross).append(entry)

        for query, form, pair in off_queries:
            for who in OWNERS:
                user_id = members[who]
                rows = await rows_for(query, user_id, conn)
                entry = entry_shape(query, form, pair)
                entry.update({
                    "as": who,
                    "user_id": user_id,
                    "owned_records": owned.get(user_id, 0),
                    "expect_count": 0,
                    "expect_names": [],
                    "results": [
                        {
                            "rank": i,
                            "record_id": r["record_id"],
                            "name": name_by_record.get(
                                r["record_id"], f"record={r['record_id']}"
                            ),
                            "sim": round(float(r["similarity"]), 6),
                            "is_expected": False,
                        }
                        for i, r in enumerate(rows, 1)
                    ],
                })
                out_off.append(entry)

    for label, rows_ in (
        ("정답 있음", out_word), ("교차 통제", out_cross), ("무관 통제", out_off)
    ):
        log(f"  {label:<8} {len(rows_):>4}행")

    hits = [x["sim"] for q in out_word for x in q["results"] if x["is_expected"]]
    off1 = [q["results"][0]["sim"] for q in out_off if q["results"]]
    if hits and off1:
        log(f"\n  정답 최솟값 {min(hits):.4f}  ·  중앙값 {sorted(hits)[len(hits) // 2]:.4f}")
        log(f"  무관 top-1 최댓값 {max(off1):.4f}")
        log(f"  → 간격 {min(hits) - max(off1):+.4f}"
            + ("   역전" if min(hits) < max(off1) else ""))

    return {
        "ticket": "S15P11A705-273",
        "profile": settings.embedding_profile,
        "model": settings.embedding_model,
        "owners": list(OWNERS),
        "record_count": len(rec_rows),
        "min_token_chars": MIN_TOKEN_CHARS,
        "cut": {
            "tau_abs": settings.search_similarity_floor,
            "tau_word": settings.search_similarity_floor_word,
            "ratio": settings.search_top_ratio,
            "word_max_chars": settings.search_word_query_max_chars,
        },
        "pair_count": len(pairs),
        "word_count": len(out_word),
        "cross_count": len(out_cross),
        "offtopic_count": len(out_off),
        "queries": out_word,
        "cross": out_cross,
        "offtopic": out_off,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / ".search" / "boundary_grid.json"))
    ap.add_argument("--dry", action="store_true", help="질의만 낸다. GMS 를 부르지 않는다")
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
        data = await build(db, settings, args.dry)
    finally:
        await db.disconnect()

    if not data:
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 행이 1,128 개라 들여쓰기만으로 2.7MB 가 된다. 기계가 읽는 파일이므로 압축해
    # 저장한다 — `word_grid.json` 과 담는 수준은 같고 형식만 다르다.
    out.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    log(f"\n  → {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""문자열 매치 artifact 생성 — 문자열 검색 병합 규칙 실측용 (P49 §8 작업 3).

기록 본문에 질의 문자열이 그대로 있는지를 (질의 × 소유자 × Record) 단위로 계산해
`.search/lexical_matrix.json` 으로 굳힌다. 이후 `lexical_sweep.py` 는 이 파일과 기존
행렬만 읽고 DB 를 부르지 않는다.

    python tools/search_cut/lexical_matrix.py

## 왜 이 artifact 가 필요한가

문자열 검색(P49 §3)의 병합 규칙은 back 이 구현하기 전에 오프라인으로 정해야 한다.
그런데 기존 행렬(`word_grid.json` 등)은 벡터 유사도만 담고 있어 「본문에 문자열이
있는가」를 재구성할 수 없다. 이 파일이 그 정보를 채운다.

## 본문은 저장하지 않는다

본문(`core.context.body`)은 DB 에서 읽어 매치 판정에만 쓰고 버린다. artifact 에는
Record id 와 매치 여부(부분일치 · 어절 시작 경계 일치)만 남는다 — 기존 행렬이
장소명까지만 담는 것과 같은 원칙이다.

## 측정 도구의 core 접근에 대해

런타임 코드(FastAPI)는 공용 계약상 `core.*` 를 읽지 않는다. 측정 도구는 런타임이
아니며, `tools/tau_grid/matrix.py` 가 같은 JOIN 을 이미 쓴다 — 그 선례를 따른다.

## 스냅샷 DB 를 쓴다

검색 고도화 트랙의 측정은 시연 DB 가 아니라 스냅샷 DB(:25432)에서 한다 — 브랜치는
코드만 격리하고 DB 는 격리하지 않기 때문이다(P49 §6). 포트가 다르면 재지 않고 멈춘다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SEARCH = ROOT / ".search"

# 검색 고도화 측정 정본은 스냅샷 DB 다(P49 §6). 시연 DB(:15432)를 직접 재면
# 측정과 시연이 같은 데이터를 공유해 부수효과가 섞인다.
EXPECT_PORT = "25432"

MATRICES = ("matrix.json", "word_grid.json", "recall_probe.json")

# 모집단은 검색 Query 와 같다(personal-search.md §4) — 검색 후보가 아닌 Context 의
# 본문에 문자열이 있어도 검색은 그 Record 를 반환할 수 없으므로 세지 않는다.
_CONTEXTS = """
SELECT e.record_id, e.user_id, e.context_id, c.body
FROM ai.context_embedding e
JOIN ai.context_ai_state s ON s.context_id = e.context_id
JOIN core.context c ON c.id = e.context_id
WHERE e.is_deleted = false
  AND e.embedding_profile = $1
  AND s.embedding_status = 'COMPLETED'
ORDER BY e.user_id, e.record_id, e.context_id
"""


def log(msg: str = "") -> None:
    print(msg, flush=True)


def collect_queries() -> tuple[list[str], dict]:
    """행렬 셋에서 질의를 모은다. 손으로 다시 적으면 행렬과 어긋난다(keyword_matrix 와 동일)."""
    queries: list[str] = []
    seen: set[str] = set()
    meta: dict = {}
    for name in MATRICES:
        p = SEARCH / name
        if not p.exists():
            raise SystemExit(f"행렬이 없다: {p.relative_to(ROOT)} — 먼저 그것부터 뜬다")
        d = json.loads(p.read_text(encoding="utf-8"))
        meta[name] = {"profile": d.get("profile"), "record_count": d.get("record_count")}
        for section in ("queries", "cross", "offtopic"):
            for e in d.get(section, []) or []:
                q = e["query"]
                if q not in seen:
                    seen.add(q)
                    queries.append(q)
    profiles = {m["profile"] for m in meta.values()}
    if len(profiles) != 1:
        raise SystemExit(f"행렬의 Profile 이 어긋난다: {meta} — 재지 않고 멈춘다")
    return queries, meta


def boundary_match(query: str, body: str) -> bool:
    """어절 시작 경계 매치 — 매치 시작 위치의 앞이 문자열 처음이거나 공백이면 참.

    「신한은행」·「신한에서」(조사·합성)는 통과하고 「대신한」은 차단한다. 교착어 특성상
    어절 끝 경계는 요구하지 않는다(P49 §5). 공백 판정은 유니코드 전체다 — `_is_word_query`
    가 전각 공백을 공백으로 보는 것과 기준을 맞춘다.
    """
    return re.search(r"(?:^|\s)" + re.escape(query), body) is not None


async def build() -> dict:
    settings = get_settings()
    url = settings.database_url
    if f":{EXPECT_PORT}/" not in url:
        raise SystemExit(
            f"DATABASE_URL 이 스냅샷 DB(:{EXPECT_PORT})가 아니다: 측정을 멈춘다.\n"
            "  검색 고도화 측정은 스냅샷에서 한다(P49 §6) — 시연 DB 를 직접 재지 않는다."
        )

    queries, meta = collect_queries()
    profile = next(iter({m["profile"] for m in meta.values()}))

    db = Database(url)
    await db.connect()
    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(_CONTEXTS, profile)
    finally:
        await db.disconnect()

    if not rows:
        raise SystemExit("검색 가능한 Context 가 0건이다 — 스냅샷이 비었다. 재지 않고 멈춘다.")

    # (user, record) -> [body...]  본문은 이 함수 밖으로 나가지 않는다.
    bodies: dict[tuple[int, int], list[str]] = {}
    for r in rows:
        bodies.setdefault((r["user_id"], r["record_id"]), []).append(r["body"])

    out_queries = []
    for q in queries:
        needle = q.strip()
        owners: dict[str, list] = {}
        for (uid, rid), bs in bodies.items():
            sub = any(needle in b for b in bs)
            if not sub:
                continue
            bound = any(boundary_match(needle, b) for b in bs)
            owners.setdefault(str(uid), []).append(
                {"record_id": rid, "substring": True, "boundary": bound}
            )
        out_queries.append({"query": q, "matches": owners})

    n_match = sum(len(v) for e in out_queries for v in e["matches"].values())
    log(f"  질의 {len(queries)}건 × Record {len(bodies)}쌍 — 매치 {n_match}건")

    return {
        "stage": "P49-lexical",
        "profile": profile,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_port": EXPECT_PORT,
        "source": {"matrices": meta},
        "record_pairs": len(bodies),
        "queries": out_queries,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="문자열 매치 artifact (P49 작업 3)")
    ap.add_argument("--out", default=str(SEARCH / "lexical_matrix.json"))
    args = ap.parse_args()

    data = asyncio.run(build())
    out = Path(args.out)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"  → {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

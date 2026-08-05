"""Keyword 신호 artifact 생성 — P48 1단계 §4.

`matrix.json` · `word_grid.json` · `recall_probe.json` 은 **벡터 유사도만** 담는다. 여기에
keyword 신호를 붙이려면 셋이 더 필요하고, 그것을 이 스크립트가 별도 artifact 로 만든다.

    질의별 **전체 활성 Preset** 코사인      top-k · 하한을 sweep 에서 바꾸려면 전량이 필요하다
    Context 별 keyword · confidence · 상태   confidence 는 **NULL 을 그대로 보존한다**
    Preset 의 version · visibility          BLOCKED 판정용(P48 §1-c)

**호출은 GMS 임베딩 배치 1회 + DB 읽기다**(`matrix.py` 와 같은 수준). 이후 `fusion_sweep.py`
는 이 파일만 읽고 GMS·DB 를 부르지 않는다.

    python tools/search_cut/keyword_matrix.py

## 질의를 손으로 적지 않는다

질의 목록을 이 파일에 다시 쓰면 행렬과 어긋난다 — 어긋나면 fusion sweep 이 조인하지 못하고,
그 사실이 「신호가 없다」로 조용히 나타난다. **기존 artifact 에서 읽어 온다.** 행렬이 재는
질의와 여기서 재는 질의가 같다는 것이 구조로 보장된다.

## 왜 질의 벡터가 아니라 코사인을 담나

이번 실험의 조절 대상은 `query→Preset` 의 **top-k 와 하한**이고, 둘 다 「Preset 별 코사인
목록을 자르는」 연산이다. 목록을 통째로 담으면 sweep 에서 자유롭게 바꿀 수 있다.

질의 벡터(1536 float)를 담으면 Preset 이 바뀌어도 재계산할 수 있다는 이점이 있으나,
Preset 이 개정되면 `preset_version` 이 바뀌고 그때는 **어차피 행렬을 다시 떠야 한다.**
그래서 이점이 실제 상황에서 크지 않다(P48 §4.2).

## 낡은 artifact 를 감지한다

`profile` · `preset_version` · Record 수 · 생성 조건을 함께 적는다. `fusion_sweep.py` 가
행렬의 값과 대조해 **어긋나면 계산하지 않고 실패한다.** 낡은 조합으로 낸 수치는 근거가
아니라 오답이다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.client.embedding_client import EmbeddingClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SEARCH = ROOT / ".search"

# 시연 정본은 15432 다(T33). `matrix.py`·`word_matrix.py`·`recall_probe.py` 와 같은 가드 —
# 데이터가 없는 DB 를 재면 「keyword 신호가 아무 데도 없다」가 결론으로 나온다.
EXPECT_PORT = "15432"

MATRICES = ("matrix.json", "word_grid.json", "recall_probe.json")

# Preset 은 `is_active` 로 적재 범위가 정해진다(keyword-preset.md §2). `visibility` 는
# **거르지 않고 담는다** — BLOCKED 제외는 소비 시점 판단이고, 낡은 데이터에 BLOCKED 행이
# 남아 있을 가능성을 방어하려면 artifact 가 그 사실을 담고 있어야 한다(P48 §1-c).
_PRESETS = """
SELECT id, code, version, visibility, embedding
FROM ai.keyword_preset
WHERE is_active = true AND embedding_profile = $1
ORDER BY id
"""

# `confidence` 는 NULL 을 그대로 가져온다. 0 으로 치환하면 「판정된 적 없음」과 구분이
# 사라진다(P48 §1-d). `keyword_status` 는 Context 제외가 아니라 **신호 제외** 판단에 쓴다.
#
# **모집단은 검색 Query 와 같아야 한다**(personal-search.md §4). 두 상태의 역할이 다르다.
#
#   embedding_status = COMPLETED   **검색 후보 자체의 조건.** 검색 Query 가 이 조건을 걸므로
#                                  미완료 Context 는 애초에 결과에 없다. 여기서 빼지 않으면
#                                  벡터 행렬에 없는 Record 가 keyword 신호로만 올라와
#                                  「코사인 없는 후보」가 되고, `similarity` 의 cosine float
#                                  계약(P48 §2.3)이 깨진다
#   keyword_status                 **신호의 조건일 뿐이다.** 미완료여도 Context 는 남기고
#                                  신호만 뺀다(§1-b). 그래서 WHERE 가 아니라 컬럼으로 싣는다
_CONTEXTS = """
SELECT e.context_id,
       e.record_id,
       e.user_id,
       s.keyword_status,
       k.keyword_id,
       k.confidence,
       k.preset_version
FROM ai.context_embedding e
JOIN ai.context_ai_state s ON s.context_id = e.context_id
LEFT JOIN ai.context_keyword k ON k.context_id = e.context_id
WHERE e.is_deleted = false
  AND e.embedding_profile = $1
  AND s.embedding_status = 'COMPLETED'
ORDER BY e.context_id, k.keyword_id
"""


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def collect_queries() -> tuple[list[str], dict]:
    """행렬 셋에서 질의를 모은다. **여기서 손으로 적지 않는다.**"""
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


def _parse_vector(raw) -> list[float]:
    """pgvector 의 반환형은 코덱 등록 여부에 따라 다르다 — 세 형태를 모두 받는다.

    `app.core.db.Database` 는 커넥션에 pgvector 코덱을 등록하므로 `Vector` 객체로 오고
    (iterable 이 아니라 `to_list()` 로 꺼낸다 — T17·T76), raw asyncpg 는 문자열로 온다.
    """
    if isinstance(raw, str):
        return [float(x) for x in raw.strip("[]").split(",")]
    if hasattr(raw, "to_list"):
        return [float(x) for x in raw.to_list()]
    return [float(x) for x in raw]


async def build(db: Database, settings, queries: list[str], meta: dict) -> dict:
    async with db.acquire() as conn:
        preset_rows = await conn.fetch(_PRESETS, settings.embedding_profile)
        ctx_rows = await conn.fetch(_CONTEXTS, settings.embedding_profile)

    if not preset_rows:
        raise SystemExit(
            "활성 Preset 이 0건이다 — 부트스트랩이 안 된 DB 다. 재지 않고 멈춘다."
        )

    versions = {r["version"] for r in preset_rows}
    if len(versions) != 1:
        raise SystemExit(
            f"Preset version 이 섞여 있다: {sorted(versions)} — "
            "판정 세트가 한 판이어야 신호를 조인할 수 있다. 재지 않고 멈춘다."
        )
    preset_version = versions.pop()

    presets = [
        {"id": r["id"], "code": r["code"], "version": r["version"],
         "visibility": r["visibility"]}
        for r in preset_rows
    ]
    vis = {}
    for p in presets:
        vis[p["visibility"]] = vis.get(p["visibility"], 0) + 1
    log(f"  Preset {len(presets)}건 · version={preset_version} · {vis}")

    preset_vecs = [(r["id"], _parse_vector(r["embedding"])) for r in preset_rows]

    # Context 를 접는다. LEFT JOIN 이라 keyword 가 없는 Context 는 keyword_id 가 NULL 이다.
    contexts: dict[int, dict] = {}
    for r in ctx_rows:
        c = contexts.setdefault(r["context_id"], {
            "context_id": r["context_id"],
            "record_id": r["record_id"],
            "user_id": r["user_id"],
            "keyword_status": r["keyword_status"],
            "keywords": [],
        })
        if r["keyword_id"] is not None:
            c["keywords"].append({
                "keyword_id": r["keyword_id"],
                # **NULL 을 그대로 둔다.** 0 으로 치환하지 않는다(P48 §1-d).
                "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
                "preset_version": r["preset_version"],
            })

    n_kw = sum(len(c["keywords"]) for c in contexts.values())
    n_null = sum(1 for c in contexts.values() for k in c["keywords"] if k["confidence"] is None)
    n_incomplete = sum(1 for c in contexts.values() if c["keyword_status"] != "COMPLETED")
    log(f"  Context {len(contexts)}건 · keyword {n_kw}건 "
        f"(confidence NULL {n_null}건) · keyword_status≠COMPLETED {n_incomplete}건")

    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    log(f"  GMS 임베딩 배치 1회 ({len(queries)}건) …")
    vecs = await client.embed(queries)

    query_preset = [
        {
            "query": q,
            # **전체 활성 Preset** 을 담는다 — top-k·하한이 sweep 의 조절 축이므로
            # 여기서 자르면 그 축이 사라진다(P48 §4.2).
            "cos": [{"preset_id": pid, "cos": round(_cos(qv, pv), 6)}
                    for pid, pv in preset_vecs],
        }
        for q, qv in zip(queries, vecs)
    ]

    return {
        "stage": "P48-1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": settings.embedding_profile,
        "model": settings.embedding_model,
        "preset_version": preset_version,
        "source": {
            "db_port": EXPECT_PORT,
            "gms_batch": 1,
            "matrices": meta,
        },
        "preset_count": len(presets),
        "context_count": len(contexts),
        "query_count": len(queries),
        "presets": presets,
        "contexts": sorted(contexts.values(), key=lambda c: c["context_id"]),
        "query_preset": query_preset,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description="Keyword 신호 artifact (P48 1단계)")
    ap.add_argument("--out", default=str(SEARCH / "keyword_matrix.json"))
    args = ap.parse_args()

    settings = get_settings()
    if EXPECT_PORT not in settings.database_url:
        raise SystemExit(
            f"DATABASE_URL 이 :{EXPECT_PORT} 를 가리키지 않는다 — "
            f"{settings.database_url.rsplit('@', 1)[-1]}\n"
            f"시연 정본은 :{EXPECT_PORT}(pinlog-demo)다(T33). 재지 않고 멈춘다."
        )

    queries, meta = collect_queries()
    if meta["matrix.json"]["profile"] != settings.embedding_profile:
        raise SystemExit(
            f"행렬 Profile({meta['matrix.json']['profile']})과 설정"
            f"({settings.embedding_profile})이 다르다 — 재지 않고 멈춘다."
        )

    log(f"  profile={settings.embedding_profile}")
    log(f"  질의 {len(queries)}건 (행렬 {len(MATRICES)}종에서 수집, 중복 제거)")

    db = Database(settings.database_url)
    await db.connect()
    try:
        data = await build(db, settings, queries, meta)
    finally:
        await db.disconnect()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n  → {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

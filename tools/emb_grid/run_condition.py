"""임베딩 4조건 중 **한 조건**을 실경로로 재고 결과를 JSON 으로 남긴다.

    python tools/emb_grid/run_condition.py A --prepare   # 서버 기동 **전**
    python tools/emb_grid/run_condition.py A             # 서버 기동 **후**

두 단계로 갈린 것은 **FastAPI 가 프리셋 없이는 뜨지 않기 때문이다.** `main.py` 의 lifespan 이
현재 profile 로 적재된 Keyword Preset 이 0건이면 기동을 거부한다(그렇지 않으면 판정이 후보를
못 찾는 채로 서버가 정상인 척한다). 조건마다 profile 이 달라지므로 적재가 기동보다 앞이다.
`--prepare` 가 토큰 로그를 새로 열고 프리셋을 이 조건의 profile 로 다시 적재한다.

한 조건만 도는 이유는 조건마다 **프로세스를 다시 띄워야** 하기 때문이다 — 임베딩 모델·차원은
FastAPI 기동 시 설정으로 굳고, 장소명 결합은 back 기동 시 굳는다. 기동까지 이 스크립트가
떠맡으면 실패 지점이 한 덩어리로 뭉쳐 어디서 어긋났는지 읽을 수 없다. 기동은 셸이 하고
(`README.md`), 이 스크립트는 **떠 있는 것이 조건과 맞는지 먼저 확인한 뒤** 잰다.

재는 것 넷:

    정확도    `demo_data.yaml` 의 질의 12건. 1위 일치를 세되 순위·유사도·2위와의 차도 남긴다
    시간      시딩 소요(`seed.py` 벽시계). GMS 왕복이 지배한다
    토큰      `PINLOG_TOKEN_LOG` JSONL 을 kind 별로 집계
    저장 비용  `ai.context_embedding` 실측 바이트. 차원이 2배면 벡터도 2배다

**사전 검증이 이 스크립트의 절반이다.** 조건이 어긋난 채 돌면 숫자는 나오는데 그 숫자가
어느 조건의 것인지 알 수 없고, 네 조건을 비교하는 것이 목적이므로 그 오염은 측정 전체를
무효로 만든다. 그래서 어긋나면 재지 않고 즉시 멈춘다.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools" / "demo_seed"))

from conditions import CONDITIONS  # noqa: E402

# `_client` 는 import 시점에 레포 루트로 chdir 하고 `get_settings()` 를 캐시한다.
from _client import (  # noqa: E402
    DEMO_PROVIDER,
    SETTINGS,
    ai_base,
    load_data,
)

import httpx  # noqa: E402

from app.core.db import Database  # noqa: E402

OUT_DIR = ROOT / ".grid"

for _s in (sys.stdout, sys.stderr):
    # seed.py·verify.py 와 같은 이유. 콘솔이 cp949 면 질의 한 글자에 측정이 죽는다.
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


def head(title: str) -> None:
    log("\n" + "=" * 78)
    log(title)
    log("=" * 78)


# ── 사전 검증 ───────────────────────────────────────────────────────────────


async def preflight(cond, ai: str, db: Database) -> None:
    """조건과 실행 환경이 어긋나면 재지 않고 멈춘다.

    셋을 본다 — 이 프로세스의 설정, DB 컬럼 차원, FastAPI 기동 여부. FastAPI 프로세스의
    설정 자체는 밖에서 읽을 수 없으므로(`/ready` 는 상태만 준다) 대신 **검색을 한 번
    쏴서** 확인한다. profile 이 어긋나면 422 가 오고, 그것이 곧 불일치의 증거다.
    """
    head(f"사전 검증 — 조건 {cond.key} ({cond.label})")

    problems: list[str] = []

    if SETTINGS.embedding_model != cond.model:
        problems.append(f"이 프로세스 model={SETTINGS.embedding_model} ≠ {cond.model}")
    if SETTINGS.embedding_dimension != cond.dimension:
        problems.append(
            f"이 프로세스 dimension={SETTINGS.embedding_dimension} ≠ {cond.dimension}"
        )
    if SETTINGS.embedding_profile != cond.profile:
        problems.append(f"이 프로세스 profile={SETTINGS.embedding_profile} ≠ {cond.profile}")

    async with db.acquire() as conn:
        # 프리셋이 이 조건 profile 로 적재됐는가. FastAPI 는 이것 없이 뜨지 않으므로 여기서
        # 걸리면 대개 `--prepare` 를 건너뛴 것이다.
        presets = await conn.fetchval(
            "SELECT count(*) FROM ai.keyword_preset "
            "WHERE embedding_profile = $1 AND is_active",
            cond.profile,
        )
        if not presets:
            problems.append(
                f"ai.keyword_preset 에 profile={cond.profile} 인 활성 행이 0건 "
                "— run_condition.py --prepare 를 먼저 돌려라"
            )

        for table in ("context_embedding", "keyword_preset"):
            dim = await conn.fetchval(
                "SELECT a.atttypmod FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'ai' AND c.relname = $1 AND a.attname = 'embedding'",
                table,
            )
            if dim != cond.dimension:
                problems.append(
                    f"ai.{table}.embedding = vector({dim}) ≠ vector({cond.dimension}) "
                    "— alter_dim.py 로 맞춰라"
                )

    try:
        r = httpx.get(f"{ai}/ready", timeout=5.0)
        ready = r.status_code
    except httpx.HTTPError as exc:
        problems.append(f"FastAPI 도달 실패: {type(exc).__name__} — 기동했는가")
        ready = None
    if ready is not None and ready not in (200, 503):
        problems.append(f"FastAPI /ready → HTTP {ready}")

    if problems:
        for p in problems:
            log(f"  [FAIL] {p}")
        raise SystemExit(f"\n조건 {cond.key} 환경이 어긋났다. 재지 않고 멈춘다.")

    log(f"  [OK] model={cond.model} dim={cond.dimension}")
    log(f"  [OK] profile={cond.profile}")
    log(f"  [OK] keyword_preset {presets}행이 이 profile 로 적재돼 있다")
    log(f"  [OK] ai.context_embedding · ai.keyword_preset = vector({cond.dimension})")
    log(f"  [OK] FastAPI /ready → {ready}" + (" (프리셋 미적재)" if ready == 503 else ""))
    log(f"  [--] include_place_name={cond.include_place_name} — back 기동 env 로 확인한다")


# ── 단계 ────────────────────────────────────────────────────────────────────


def run(argv: list[str], what: str) -> float:
    """자식 프로세스를 돌리고 벽시계 소요를 돌려준다. 실패하면 멈춘다."""
    head(what)
    started = time.monotonic()
    proc = subprocess.run(argv, cwd=ROOT, env=os.environ.copy())
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        raise SystemExit(f"{what} 실패 (exit {proc.returncode}). 재지 않고 멈춘다.")
    log(f"\n  {what} — {elapsed:.1f}s")
    return elapsed


async def measure_search(ai: str, db: Database, data: dict) -> dict:
    """질의 12건. 1위 일치를 세되 순위·유사도·2위와의 차를 함께 남긴다.

    `verify.py` 의 A절과 같은 것을 재지만 출력이 다르다 — 저쪽은 시연 직전 PASS/FAIL 점검이고
    이쪽은 조건 간 비교표를 만들어야 하므로 질의별 수치가 구조화되어 남아야 한다.
    """
    head("정확도 — 자연어 검색 12건")

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
                "SELECT ctx.id AS context_id, p.name AS name FROM core.context ctx "
                "JOIN core.record r ON r.id = ctx.record_id "
                "JOIN core.place p ON p.id = r.place_id "
                "WHERE ctx.member_id = ANY($1::bigint[])",
                list(members.values()),
            )
        }

    expect_place = {
        rec["key"]: rec["place"]["name"] for m in data["members"] for rec in m["records"]
    }

    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for q in data["demo_queries"]:
            who = q.get("as", "host")
            resp = await client.post(
                f"{ai}/internal/v1/search",
                headers={"X-Internal-Secret": SETTINGS.internal_shared_secret},
                json={
                    "userId": members[who],
                    "query": q["query"],
                    "limit": 5,
                    "embeddingProfile": SETTINGS.embedding_profile,
                },
            )
            if resp.status_code != 200:
                # 422 는 profile 불일치다. 사전 검증이 못 잡는 유일한 경로이므로 여기서 죽인다.
                raise SystemExit(
                    f"검색 실패 HTTP {resp.status_code} — {resp.text[:200]}\n"
                    "422 면 FastAPI 프로세스의 profile 이 이 조건과 다르다."
                )
            results = resp.json()["results"]
            want = expect_place[q["expect"]]
            ranked = [
                (place_by_ctx.get(r["contextId"], f"ctx={r['contextId']}"), r["similarity"])
                for r in results
            ]
            top_name, top_sim = ranked[0] if ranked else (None, None)
            # 기대한 Record 가 몇 위인지. 1위 일치가 아니어도 top-3 안에 있으면 성격이 다르다.
            want_rank = next((i for i, (n, _) in enumerate(ranked, 1) if n == want), None)
            rows.append(
                {
                    "query": q["query"],
                    "as": who,
                    "expect": want,
                    "top": top_name,
                    "similarity": top_sim,
                    "margin": (top_sim - ranked[1][1]) if len(ranked) > 1 else None,
                    "want_rank": want_rank,
                    "hit": top_name == want,
                }
            )
            mark = "PASS" if rows[-1]["hit"] else f"FAIL (기대 {want} — {want_rank}위)"
            log(f"  {q['query'][:34]:<36} → {str(top_name)[:20]:<22} {top_sim:.4f}  {mark}")

    hits = sum(1 for r in rows if r["hit"])
    top3 = sum(1 for r in rows if r["want_rank"] is not None and r["want_rank"] <= 3)
    log(f"\n  1위 일치 {hits}/{len(rows)}   top-3 {top3}/{len(rows)}")
    return {"rows": rows, "hits": hits, "total": len(rows), "top3": top3}


def measure_tokens(path: Path) -> dict:
    """조건별 JSONL 을 kind 로 집계한다. 파일은 조건마다 새로 만든다."""
    head("토큰")
    if not path.exists():
        log(f"  토큰 로그가 없다: {path}")
        return {}
    agg: dict[str, dict] = defaultdict(lambda: {"calls": 0, "prompt": 0, "output": 0, "total": 0})
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        a = agg[row["kind"]]
        a["calls"] += 1
        for k in ("prompt", "output", "total"):
            if row.get(k):
                a[k] += row[k]
    for kind, a in agg.items():
        per = a["total"] / a["calls"] if a["calls"] else 0
        log(f"  {kind:<10} {a['calls']:>4}회  {a['total']:>7,} 토큰  건당 {per:>7.1f}")
    grand = sum(a["total"] for a in agg.values())
    log(f"  {'합계':<10} {sum(a['calls'] for a in agg.values()):>4}회  {grand:>7,} 토큰")
    return {"by_kind": dict(agg), "total": grand}


async def measure_storage(db: Database) -> dict:
    """저장 비용. 차원이 2배면 벡터도 2배라는 것을 실측으로 확인한다."""
    head("저장 비용 — ai.context_embedding")
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT count(*) AS rows, "
            "       coalesce(avg(pg_column_size(embedding)), 0)::bigint AS avg_vec, "
            "       pg_total_relation_size('ai.context_embedding') AS total_bytes "
            "FROM ai.context_embedding"
        )
        presets = await conn.fetchval("SELECT count(*) FROM ai.keyword_preset")
    log(f"  행        {row['rows']}")
    log(f"  벡터 1건   {row['avg_vec']:,} bytes")
    log(f"  테이블 전체 {row['total_bytes']:,} bytes (인덱스·TOAST 포함)")
    log(f"  프리셋     {presets}행")
    return {
        "rows": row["rows"],
        "avg_vector_bytes": row["avg_vec"],
        "total_relation_bytes": row["total_bytes"],
        "preset_rows": presets,
    }


# ── main ────────────────────────────────────────────────────────────────────


async def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0].upper() not in CONDITIONS:
        log(f"사용: python tools/emb_grid/run_condition.py [{'|'.join(CONDITIONS)}]")
        return 2
    cond = CONDITIONS[argv[0].upper()]
    ai = ai_base(sys.argv)

    OUT_DIR.mkdir(exist_ok=True)
    token_log = Path(os.environ.get("PINLOG_TOKEN_LOG", OUT_DIR / f"tokens-{cond.key}.jsonl"))
    os.environ["PINLOG_TOKEN_LOG"] = str(token_log)

    if "--prepare" in argv:
        # 조건마다 새로 센다. 앞 조건의 행이 남아 있으면 토큰이 누적돼 조건 비교가 무너진다.
        if token_log.exists():
            token_log.unlink()
        # `keyword_preset` 은 `code` UNIQUE 라 profile 별로 공존하지 못한다(UPSERT 가 덮어쓴다).
        # 적재된 profile 이 현재 설정과 다르면 `load_active` 가 0건을 내고 FastAPI 는 아예
        # 뜨지 않는다. 프리셋 임베딩 토큰도 이 조건의 비용이므로 같은 로그에 쌓인다.
        run([sys.executable, "-m", "app.bootstrap.load_presets"], f"프리셋 적재 — 조건 {cond.key}")
        log(f"\n  준비 완료. 이제 FastAPI 와 back 을 이 조건 env 로 띄워라.")
        return 0

    db = Database(SETTINGS.database_url)
    await db.connect()
    try:
        await preflight(cond, ai, db)

        seed_sec = run(
            [sys.executable, "tools/demo_seed/seed.py", "--reset", "--pace", "1"],
            "시딩 (37건)",
        )

        search = await measure_search(ai, db, load_data())
        tokens = measure_tokens(token_log)
        storage = await measure_storage(db)
    finally:
        await db.close()

    result = {
        "condition": cond.key,
        "label": cond.label,
        "model": cond.model,
        "dimension": cond.dimension,
        "include_place_name": cond.include_place_name,
        "profile": cond.profile,
        "seconds": {"seed": round(seed_sec, 1)},
        "accuracy": search,
        "tokens": tokens,
        "storage": storage,
    }
    out = OUT_DIR / f"condition-{cond.key}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    head(f"조건 {cond.key} 완료 — {out}")
    log(f"  정확도 {search['hits']}/{search['total']} · 시딩 {seed_sec:.0f}s · "
        f"{tokens.get('total', 0):,} 토큰 · 벡터 {storage['avg_vector_bytes']:,}B")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

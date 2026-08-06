"""LLM 질의 재작성이 검색 결과를 어떻게 바꾸는지 실측하는 프로브 (S15P11A705-337 잔여).

재작성 런타임(P49 §3의 LLM 질의 재작성, 커밋 39dba0d)은 구현됐지만, 실제 LLM 이 만든
재작성문으로 검색했을 때의 효과는 재지 않았다. 이 프로브가 그 잔여를 잰다. 재는 것은 둘이다.

    ① 관련 없는 질의 15건 — 재작성을 거쳐도 검색 결과가 노출되지 않는가.
       채택 조건: 무노출 질의 수가 현행(15건 중 11건) 이상이어야 한다.
    ② 실패 사례 5건(`부캠`·`신한 부캠`·`신한`·`그네`·`스팟`) — 재작성이 기대 정답의
       컷 전 순위·컷 통과 여부를 어떻게 움직이는가.

**재작성문 자체를 결과에 기록한다.** 무엇으로 바뀌었는지가 판정 근거의 절반이다.

원문 기준(재작성 전)의 값은 굳힌 행렬(`matrix.json`·`recall_probe.json`)에서 컷을
재구성해 얻는다. GMS 를 부르는 것은 재작성 LLM 호출 20건과 재작성문 임베딩 배치 1회다.

    $env:DATABASE_URL="postgresql://…:25432/…"; python tools/search_cut/rewrite_probe.py

측정은 스냅샷 DB(:25432, `pinlog-search-upgrade-pg`)에서 한다. 시연 DB(:15432)와 e2e
DB(:5433)를 가리키면 실행을 멈춘다 — 시연 DB 는 검증 게이트 전 접근 금지이고, 다른 DB 는
데이터가 달라 결론이 오염된다.
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
from app.client.retry import RetryPolicy  # noqa: E402
from app.client.rewrite_client import RewriteClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.core.errors import PermanentError, TransientError  # noqa: E402
from app.repository import context_embedding_repo  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    # T28. 콘솔이 cp949 면 장소명 한 글자에 측정이 죽는다.
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


class GuardError(SystemExit):
    pass


SEARCH = ROOT / ".search"

# 검색 고도화 측정은 전부 스냅샷 DB 에서 한다(인계 §7). recall_probe.py 의 15432 가드와
# 값이 다른 것은 대상 DB 가 다르기 때문이다 — 그쪽은 -255 시절 시연 DB 실측이고, 이
# 트랙은 시연 DB 쓰기 접근이 게이트 전 금지라 스냅샷 사본을 쓴다.
EXPECT_PORT = "25432"

# 컷 전 순위를 보려면 서비스 limit(20)보다 커야 한다. recall_probe.py 와 같은 값.
NO_LIMIT = 10_000

# 서비스 기본 limit(공용 계약 08 §6.1). 컷 재구성의 절단 기준이다.
SERVICE_LIMIT = 20

# 실패 사례 5건. 기대 정답은 recall_probe.json 의 expect 를 그대로 쓴다.
CASES = ("부캠", "신한 부캠", "신한", "그네", "스팟")

# 채택 조건. 관련 없는 문장형 질의 15건 중 무노출이 이 수 미만이면 재작성을 켤 수 없다.
BASELINE_SILENT = 11


def is_word_query(q: str, max_chars: int) -> bool:
    """서비스의 단어형 판정(`SearchService._is_word_query`)을 다시 적는다.

    import 하지 않는 이유는 recall_probe.py 와 같다 — 구현이 명세와 달라도 둘이 함께
    틀리면 재구성이 「일치」로 보인다.
    """
    q = q.strip()
    return bool(q) and not any(c.isspace() for c in q) and len(q) <= max_chars


def apply_cut(results: list[dict], *, query: str, settings) -> list[dict]:
    """서비스의 컷(`SearchService._cut`)을 재구성한다. SQL LIMIT 이 먼저, 컷이 뒤다.

    τ_abs 는 **판정 대상 질의**(재작성 후라면 재작성문)의 단어형 여부로 갈린다 —
    서비스가 임베딩 입력과 컷 판정 입력을 같은 텍스트로 맞추기 때문이다(search_service).
    """
    head = results[:SERVICE_LIMIT]
    if not head:
        return head
    ratio = settings.search_top_ratio
    if settings.search_similarity_floor <= 0 and ratio <= 0:
        return head
    floor = (
        settings.search_similarity_floor_word
        if is_word_query(query, settings.search_word_query_max_chars)
        else settings.search_similarity_floor
    )
    top = head[0]["sim"]
    return [r for r in head if r["sim"] >= floor and r["sim"] >= ratio * top]


def load_inputs(settings) -> tuple[list[dict], list[dict]]:
    """굳힌 행렬에서 무관 15건·사례 5건을 꺼낸다. profile 불일치면 재지 않고 멈춘다."""
    matrix = json.loads((SEARCH / "matrix.json").read_text(encoding="utf-8"))
    recall = json.loads((SEARCH / "recall_probe.json").read_text(encoding="utf-8"))

    for name, art in (("matrix.json", matrix), ("recall_probe.json", recall)):
        if art["profile"] != settings.embedding_profile:
            raise GuardError(
                f"{name} 의 profile({art['profile']})이 현행 설정"
                f"({settings.embedding_profile})과 다르다 — 행렬을 다시 뜨기 전에는 "
                "이 측정이 성립하지 않는다. 재지 않고 멈춘다."
            )

    offtopic = matrix["offtopic"]
    if len(offtopic) != 15:
        raise GuardError(f"무관 질의가 15건이 아니다: {len(offtopic)}건")

    by_query = {q["query"]: q for q in recall["queries"]}
    cases = []
    for name in CASES:
        row = by_query.get(name)
        if row is None:
            raise GuardError(f"recall_probe.json 에 사례 질의가 없다: {name}")
        cases.append({**row, "user_id": recall["user_id"]})
    return offtopic, cases


async def rewrite_all(queries: list[str], settings) -> dict[str, dict]:
    """질의 전부를 실제 RewriteClient(운영과 같은 설정 체인)로 재작성한다.

    실패는 원문 강등으로 기록한다 — 서비스와 같은 동작이며, 실패했다는 사실 자체가
    측정 결과다(강등 빈도).
    """
    client = RewriteClient(
        gms_base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        chain=settings.judge_vendors,
        timeout=settings.search_llm_timeout_sec,
        retry=RetryPolicy(attempts=settings.search_llm_attempts),
        cache_size=settings.search_rewrite_cache_size,
    )
    out: dict[str, dict] = {}
    for q in queries:
        try:
            rewritten = await client.rewrite(q)
            out[q] = {"rewritten": rewritten, "degraded": False}
        except (TransientError, PermanentError) as e:
            out[q] = {"rewritten": q, "degraded": True, "error": type(e).__name__}
        log(f"  재작성  {q!r} → {out[q]['rewritten']!r}"
            + ("  (강등: 원문 유지)" if out[q]["degraded"] else ""))
    return out


async def measure(db: Database, settings, rewrites: dict[str, dict],
                  offtopic: list[dict], cases: list[dict]) -> dict:
    texts = [rewrites[q]["rewritten"] for q in rewrites]
    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    vectors = dict(zip(rewrites.keys(), await client.embed(texts)))

    async def search_rows(user_id: int, vec) -> list[dict]:
        async with db.acquire() as conn:
            rows = await context_embedding_repo.search(
                conn, user_id, settings.embedding_profile, vec, NO_LIMIT
            )
        return [
            {"record_id": r["record_id"], "context_id": r["context_id"],
             "sim": round(float(r["similarity"]), 6)}
            for r in rows
        ]

    # ── ① 무관 질의 15건 ────────────────────────────────────────────────
    off_rows = []
    silent_before = silent_after = 0
    for item in offtopic:
        q = item["query"]
        before_kept = apply_cut(item["results"], query=q, settings=settings)
        rw = rewrites[q]
        after_all = await search_rows(item["user_id"], vectors[q])
        after_kept = apply_cut(after_all, query=rw["rewritten"], settings=settings)
        silent_before += not before_kept
        silent_after += not after_kept
        off_rows.append({
            "query": q,
            "rewritten": rw["rewritten"],
            "degraded": rw["degraded"],
            "before_returned": len(before_kept),
            "after_returned": len(after_kept),
            "after_top1_sim": after_all[0]["sim"] if after_all else None,
        })
        log(f"  무관    {q!r}: 전 {len(before_kept)}건 → 후 {len(after_kept)}건")

    # ── ② 사례 5건 ─────────────────────────────────────────────────────
    case_rows = []
    for item in cases:
        q = item["query"]
        rw = rewrites[q]
        # 전(원문): 굳힌 행렬의 전량 결과에 컷을 재구성한다.
        name_by_record = {r["record_id"]: r["name"] for r in item["results"]}
        expect_ids = {rid for rid, n in name_by_record.items() if n == item["expect"]}
        before_kept = apply_cut(item["results"], query=q, settings=settings)
        before_rank = next(
            (r["rank"] for r in item["results"] if r["record_id"] in expect_ids), None)
        before_in = any(r["record_id"] in expect_ids for r in before_kept)
        # 후(재작성문): 임베딩을 다시 떠 스냅샷 DB 를 잰다.
        after_all = await search_rows(item["user_id"], vectors[q])
        after_kept = apply_cut(after_all, query=rw["rewritten"], settings=settings)
        after_rank = next(
            (i + 1 for i, r in enumerate(after_all) if r["record_id"] in expect_ids),
            None)
        after_in = any(r["record_id"] in expect_ids for r in after_kept)
        case_rows.append({
            "query": q,
            "rewritten": rw["rewritten"],
            "degraded": rw["degraded"],
            "expect": item["expect"],
            "before": {"pre_cut_rank": before_rank, "returned": before_in,
                       "returned_count": len(before_kept)},
            "after": {"pre_cut_rank": after_rank, "returned": after_in,
                      "returned_count": len(after_kept)},
        })
        log(f"  사례    {q!r} → {rw['rewritten']!r}: 정답 컷 전 "
            f"{before_rank}위→{after_rank}위 · 반환 {before_in}→{after_in}")

    verdict = {
        "silent_before": silent_before,
        "silent_after": silent_after,
        "baseline": BASELINE_SILENT,
        "adopted": silent_after >= BASELINE_SILENT,
    }
    return {
        "ticket": "S15P11A705-337",
        "profile": settings.embedding_profile,
        "model": settings.embedding_model,
        "rewrite_chain": [f"{v}:{m}" for v, m in settings.judge_vendors],
        "cut": {
            "tau_abs": settings.search_similarity_floor,
            "tau_abs_word": settings.search_similarity_floor_word,
            "word_max_chars": settings.search_word_query_max_chars,
            "ratio": settings.search_top_ratio,
            "limit": SERVICE_LIMIT,
        },
        "offtopic": off_rows,
        "cases": case_rows,
        "verdict": verdict,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SEARCH / "rewrite_probe.json"))
    args = ap.parse_args()

    settings = get_settings()
    if EXPECT_PORT not in settings.database_url:
        raise GuardError(
            f"DATABASE_URL 이 :{EXPECT_PORT}(스냅샷 DB)를 가리키지 않는다 — "
            f"{settings.database_url.rsplit('@', 1)[-1]}\n"
            "검색 고도화 측정은 스냅샷 DB 에서만 한다. 재지 않고 멈춘다."
        )

    offtopic, cases = load_inputs(settings)
    log(f"  profile={settings.embedding_profile}")
    log(f"  재작성 체인={['%s:%s' % (v, m) for v, m in settings.judge_vendors]}"
        f" · 타임아웃={settings.search_llm_timeout_sec}s"
        f" · 시도={settings.search_llm_attempts}\n")

    queries = [o["query"] for o in offtopic] + [c["query"] for c in cases]
    rewrites = await rewrite_all(queries, settings)
    log()

    db = Database(settings.database_url)
    await db.connect()
    try:
        data = await measure(db, settings, rewrites, offtopic, cases)
    finally:
        await db.disconnect()

    v = data["verdict"]
    log(f"\n  무관 무노출: 전 {v['silent_before']}/15 → 후 {v['silent_after']}/15 "
        f"(채택 기준 {v['baseline']} 이상: {'충족' if v['adopted'] else '미달'})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  → {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

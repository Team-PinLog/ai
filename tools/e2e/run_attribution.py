"""불일치 2건의 원인 귀속.

  A. 샘플 00 후보 경계 — 10번째 슬롯 경합인가(임베딩 미세차) vs 로직 차이인가
  B. 샘플 05 판정   — 하네스의 202 추가가 계통적(프롬프트 탓)인가 확률적인가
     양 경로를 각 5회 반복해 빈도로 판정한다.
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter

import numpy as np
import yaml

from _common import ROOT, SETTINGS as S  # noqa: E402

EVAL = ROOT / "tools" / "keyword_eval"
sys.path.insert(0, str(EVAL))

from app.cache.preset_cache import PresetCache  # noqa: E402
from app.client.embedding_client import EmbeddingClient  # noqa: E402
from app.client.llm_client import LLMClient  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repository import keyword_preset_repo  # noqa: E402
from app.service.keyword_service import _to_array, _topk  # noqa: E402

import embed as H_EMBED  # noqa: E402
import test_c_judge as H_JUDGE  # noqa: E402

# 운영 판정 경로는 벤더 폴백 체인이지만(S15P11A705-175), 이 도구는 하네스와의 차이를
# 귀인하는 것이 목적이므로 **하네스와 같은 벤더·모델 하나로 고정**한다. 폴백이 걸리면
# 벤더 차이가 후보·판정 차이에 섞여 귀인 자체가 성립하지 않는다.
JUDGE_VENDOR, JUDGE_MODEL = "gemini", "gemini-2.5-flash"

REPEAT = 5


async def main() -> None:
    samples = yaml.safe_load(
        (EVAL / "samples.yaml").read_text(encoding="utf-8")
    )["samples"][:10]

    db = Database(S.database_url)
    await db.connect()
    async with db.acquire() as conn:
        rows = await keyword_preset_repo.load_active(conn, S.embedding_profile)
    cache = PresetCache()
    cache.load(rows)
    await db.disconnect()
    snap = cache.snapshot()

    ec = EmbeddingClient(base_url=S.gms_base_url, api_key=S.gms_api_key,
                         model=S.embedding_model, dimension=S.embedding_dimension)
    llm = LLMClient(gms_base_url=S.gms_base_url, api_key=S.gms_api_key,
                    chain=[(JUDGE_VENDOR, JUDGE_MODEL)])

    # ---------- A. 샘플 00 후보 경계 ----------
    print("=" * 88)
    print("A. 샘플 00 후보 경계 분석 — 10번째 슬롯에서 운영 205 vs 하네스 106")
    print("=" * 88)
    t0 = samples[0]["text"]
    pv = (await ec.embed([t0]))[0]

    presets = snap.presets
    q = _to_array(pv); q = q / np.linalg.norm(q)
    mat = np.stack([p.embedding for p in presets])
    p_sims = (mat @ q) / np.linalg.norm(mat, axis=1)
    p_rank = np.argsort(-p_sims)

    h_presets = H_EMBED.load_presets()
    h_pmat = H_EMBED.embed([H_EMBED.preset_embed_text(p) for p in h_presets])
    h_sim = H_EMBED.cosine_matrix(H_EMBED.embed([t0]), h_pmat)[0]
    h_rank = np.argsort(-h_sim)

    print(f"  {'순위':<5}{'운영 id':>8}{'운영 sim':>12}   {'하네스 id':>9}{'하네스 sim':>12}")
    for r in range(8, 13):
        pi, hi = p_rank[r], h_rank[r]
        mark = "  ← K=10 경계" if r == 9 else ""
        print(f"  {r+1:<5}{presets[pi].id:>8}{p_sims[pi]:>12.6f}   "
              f"{h_presets[hi]['id']:>9}{h_sim[hi]:>12.6f}{mark}")
    # 두 경로에서 205/106의 상대 격차
    def sim_of(ids, sims, target):
        for i, x in enumerate(ids):
            if x == target:
                return sims[i]
        return float("nan")
    p_ids_all = [p.id for p in presets]
    h_ids_all = [p["id"] for p in h_presets]
    d_prod = sim_of(p_ids_all, p_sims, 205) - sim_of(p_ids_all, p_sims, 106)
    d_harn = sim_of(h_ids_all, h_sim, 205) - sim_of(h_ids_all, h_sim, 106)
    print(f"\n  sim(205) − sim(106):  운영 {d_prod:+.6f}  ·  하네스 {d_harn:+.6f}")
    print(f"  → 부호 {'동일(로직 일치, 경계 밖 무관)' if d_prod*d_harn > 0 else '반대 = 순위 역전'}"
          f", 격차 크기 {abs(d_prod):.6f}/{abs(d_harn):.6f}")

    # ---------- B. 샘플 05 판정 반복 ----------
    print()
    print("=" * 88)
    print(f"B. 샘플 05 판정 반복 (각 {REPEAT}회) — 하네스 202 추가가 계통적인가")
    print("=" * 88)
    s5 = samples[5]
    v5 = (await ec.embed([s5["text"]]))[0]
    cands = _topk(_to_array(v5), snap, S.keyword_candidate_top_k, S.similarity_floor)
    cand_ids = [p.id for p in cands]
    cd = [{"id": p.id, "display_name": p.display_name, "category": p.category,
           "description": p.description, "examples": p.examples} for p in cands]
    print(f"  텍스트: {s5['text']}")
    print(f"  후보(동일 투입): {cand_ids}\n")

    prod_c, harn_c = Counter(), Counter()
    for i in range(REPEAT):
        r = await llm.judge(s5["text"], cd)
        sel = tuple(sorted({x.keyword_id for x in r.selected} & set(cand_ids)))
        prod_c[sel] += 1
        out, _ = H_JUDGE.judge(JUDGE_VENDOR, JUDGE_MODEL,
                               H_JUDGE.build_user(s5["text"], cd), cand_ids)
        hsel = tuple(sorted({x["keywordId"] for x in out.get("selected", [])} & set(cand_ids)))
        harn_c[hsel] += 1
        print(f"  {i+1}회차  운영 {list(sel)}   하네스 {list(hsel)}")

    print(f"\n  운영   분포: {dict(prod_c)}")
    print(f"  하네스 분포: {dict(harn_c)}")
    prod_has202 = sum(v for k, v in prod_c.items() if 202 in k)
    harn_has202 = sum(v for k, v in harn_c.items() if 202 in k)
    print(f"  202(식사) 포함 빈도: 운영 {prod_has202}/{REPEAT} · 하네스 {harn_has202}/{REPEAT}")
    if prod_has202 == 0 and harn_has202 == REPEAT:
        verdict = "계통적 — 프롬프트 차이가 결과를 바꾼다"
    elif prod_has202 == harn_has202:
        verdict = "차이 없음 — 앞선 1건은 확률적 흔들림"
    else:
        verdict = "확률적 편향 — 경계 사례에서 양쪽 모두 흔들린다"
    print(f"  → 판정: {verdict}")


asyncio.run(main())

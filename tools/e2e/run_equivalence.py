"""하네스(tools/keyword_eval) ↔ 운영(app/) 동등성 실측.

프롬프트가 이미 1줄 다르다는 것은 코드 정독으로 확정됨(운영에만 unmatchedConcepts 지시).
여기서 재는 것은 "그 차이가 결과를 바꾸는가"이며, LLM 비결정성과 구분하기 위해
운영 경로 자체의 흔들림(기준선)을 먼저 잰다.

  단계 1  비결정성 기준선  — 운영 판정을 같은 입력으로 2회
  단계 2  후보 집합 비교    — 운영(DB 적재 벡터 + _topk) vs 하네스(자체 임베딩 + argsort)
  단계 3  판정 결과 비교    — 동일 후보를 두 경로에 넣어 selected 비교
"""
from __future__ import annotations

import asyncio
import sys

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

# 이 도구가 재는 것은 "하네스와 운영이 **같은 입력에 같은 판정**을 내는가"다. 그러려면
# 양쪽 모델이 같아야 한다. 운영 판정 경로는 이제 벤더 폴백 체인이지만
# (S15P11A705-175), 폴백이 걸리면 벤더 차이가 동등성 측정에 섞여 프롬프트 1줄 차이의
# 영향과 구별할 수 없게 된다. 그래서 여기서는 **하네스와 같은 벤더·모델 하나로 고정**한다
# — 설정의 체인(`S.judge_vendors`)을 쓰지 않는 것이 의도다. 벤더별 비교는
# `tools/keyword_eval/probe_vendors.py` 의 일이다.
JUDGE_VENDOR, JUDGE_MODEL = "gemini", "gemini-2.5-flash"

N_SAMPLES = 10


def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


async def main() -> None:
    samples = yaml.safe_load(
        (EVAL / "samples.yaml").read_text(encoding="utf-8")
    )["samples"][:N_SAMPLES]
    texts = [s["text"] for s in samples]

    # ---- 운영 경로 준비: DB 적재 프리셋 + 캐시 + 실제 임베딩 클라이언트 ----
    # 앱과 동일한 연결 설정(search_path=ai,public + register_vector)을 쓴다.
    db = Database(S.database_url)
    await db.connect()
    async with db.acquire() as conn:
        rows = await keyword_preset_repo.load_active(conn, S.embedding_profile)
    cache = PresetCache()
    n = cache.load(rows)
    await db.disconnect()
    snap = cache.snapshot()

    ec = EmbeddingClient(
        base_url=S.gms_base_url, api_key=S.gms_api_key,
        model=S.embedding_model, dimension=S.embedding_dimension,
    )
    prod_vecs = await ec.embed(texts)
    llm = LLMClient(gms_base_url=S.gms_base_url, api_key=S.gms_api_key,
                    chain=[(JUDGE_VENDOR, JUDGE_MODEL)])

    print(f"운영 프리셋 캐시 {n}건 · 샘플 {len(samples)}건 · judge={JUDGE_VENDOR}:{JUDGE_MODEL}")

    # ---- 하네스 경로 준비: 자체 프리셋 임베딩 + 자체 샘플 임베딩 ----
    h_presets = H_EMBED.load_presets()
    h_pmat = H_EMBED.embed([H_EMBED.preset_embed_text(p) for p in h_presets])
    h_smat = H_EMBED.embed(texts)
    h_sim = H_EMBED.cosine_matrix(h_smat, h_pmat)
    print(f"하네스 프리셋 {len(h_presets)}건 자체 임베딩 완료\n")

    K, FLOOR = S.keyword_candidate_top_k, S.similarity_floor

    print("=" * 92)
    print("단계 1·2  비결정성 기준선 + 후보 집합 비교")
    print("=" * 92)
    base_same = cand_same = 0
    prod_runs = []
    for i, s in enumerate(samples):
        # 운영 후보
        p_cands = _topk(_to_array(prod_vecs[i]), snap, K, FLOOR)
        p_ids = [p.id for p in p_cands]
        # 하네스 후보
        order = [pi for pi in np.argsort(-h_sim[i])[:K] if h_sim[i, pi] >= FLOOR]
        h_ids = [h_presets[pi]["id"] for pi in order]

        same_set = set(p_ids) == set(h_ids)
        same_order = p_ids == h_ids
        cand_same += same_set

        # 운영 판정 2회 (비결정성 기준선)
        cd = [{"id": p.id, "display_name": p.display_name, "category": p.category,
               "description": p.description, "examples": p.examples} for p in p_cands]
        r1 = await llm.judge(s["text"], cd) if cd else None
        r2 = await llm.judge(s["text"], cd) if cd else None
        sel1 = {x.keyword_id for x in r1.selected} & set(p_ids) if r1 else set()
        sel2 = {x.keyword_id for x in r2.selected} & set(p_ids) if r2 else set()
        base_same += sel1 == sel2
        prod_runs.append((cd, p_ids, sel1, sel2, r1))

        print(f"\n[{i:02d}] {s['text'][:52]}")
        print(f"     후보 운영 : {p_ids}")
        print(f"     후보 하네스: {h_ids}")
        print(f"     후보 일치 : 집합 {'O' if same_set else 'X'} / 순서 {'O' if same_order else 'X'}"
              f"  (Jaccard {jaccard(set(p_ids), set(h_ids)):.2f})")
        print(f"     운영 판정 1회차 {sorted(sel1)} · 2회차 {sorted(sel2)} "
              f"→ {'동일' if sel1 == sel2 else '흔들림'}")

    print()
    print("=" * 92)
    print("단계 3  판정 결과 비교 — 동일 후보를 두 경로에 투입 (프롬프트·스키마만 다름)")
    print("=" * 92)
    judge_same = 0
    n_judged = 0
    for i, s in enumerate(samples):
        cd, p_ids, sel1, _, _ = prod_runs[i]
        if not cd:
            print(f"\n[{i:02d}] 후보 0개 — 양쪽 모두 LLM 미호출")
            continue
        n_judged += 1
        user = H_JUDGE.build_user(s["text"], cd)
        out, meta = H_JUDGE.judge(JUDGE_VENDOR, JUDGE_MODEL, user, p_ids)
        h_sel = {x["keywordId"] for x in out.get("selected", [])} & set(p_ids)
        same = sel1 == h_sel
        judge_same += same
        print(f"\n[{i:02d}] {s['text'][:52]}")
        print(f"     운영   selected: {sorted(sel1)}")
        print(f"     하네스 selected: {sorted(h_sel)}")
        print(f"     → {'일치' if same else '불일치'}"
              f"   (하네스 parse_error={meta['parse_error']}, {meta['latency']:.2f}s)")

    print()
    print("=" * 92)
    print("집계")
    print("=" * 92)
    print(f"  후보 집합 일치        : {cand_same}/{len(samples)}")
    print(f"  운영 자체 재현성(기준선): {base_same}/{len(samples)}  ← 이 값이 100%가 아니면")
    print("                            아래 불일치의 일부는 비결정성 탓이다")
    print(f"  운영 vs 하네스 판정 일치: {judge_same}/{n_judged}")


asyncio.run(main())

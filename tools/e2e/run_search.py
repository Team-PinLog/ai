"""검색 실경로 검증 — 계약 방어선 + 품질(분리도) + 집계·격리.

사용:
  python tools/e2e/run_search.py [--base http://localhost:8000]
  python tools/e2e/run_search.py --base http://localhost:8001   # Docker 컨테이너

한글 본문은 curl 대신 httpx로 보낸다(T24).
"""
from __future__ import annotations

import asyncio
import sys

import httpx

from _common import SETTINGS, base_url, headers, load_contexts

CTX = {c["context_id"]: c for c in load_contexts()}
PROFILE = SETTINGS.embedding_profile

# (질의, 의도한 context_id 또는 None=무관질의)
QUERIES = [
    ("친구들이랑 시끌벅적하게 놀 만한 곳", 1001),
    ("혼자 조용히 작업하기 좋은 카페", 1002),
    ("기념일에 야경 보면서 식사할 곳", 1005),
    ("비 오는 날 아늑하게 있을 만한 데", 1006),
    ("강아지 데려갈 수 있는 곳", 1004),
    ("주차 편한 곳", 1003),
    ("자동차 엔진오일 교환 정비소", None),
    ("파이썬 비동기 프로그래밍 튜토리얼", None),
]


def label(cid: int) -> str:
    c = CTX.get(cid)
    return c["place"] if c else f"ctx{cid}"


async def main() -> None:
    base = base_url(sys.argv)
    url = f"{base}/internal/v1/search"
    async with httpx.AsyncClient(timeout=60.0) as client:
        print("=" * 78)
        print(f"A. 계약 방어선  ({base})")
        print("=" * 78)
        r = await client.post(url, headers=headers(),
                              json={"userId": 9001, "query": "카페", "limit": 3,
                                    "embeddingProfile": PROFILE})
        print(f"  정상 호출      → {r.status_code}")
        r = await client.post(url, headers=headers(),
                              json={"userId": 9001, "query": "카페", "limit": 3,
                                    "embeddingProfile": "bogus-profile-v9"})
        print(f"  profile 불일치 → {r.status_code}  {r.json().get('detail')}")
        r = await client.post(url, json={"userId": 9001, "query": "카페", "limit": 3,
                                         "embeddingProfile": PROFILE})
        print(f"  secret 누락    → {r.status_code}  {r.json().get('detail')}")

        print()
        print("=" * 78)
        print("B. 검색 품질 — 질의별 결과 (limit=5)")
        print("=" * 78)
        top1_related: list[float] = []
        top1_unrelated: list[float] = []
        for q, want in QUERIES:
            r = await client.post(url, headers=headers(),
                                  json={"userId": 9001, "query": q, "limit": 5,
                                        "embeddingProfile": PROFILE})
            res = r.json()["results"]
            print(f"\n[{'무관' if want is None else '관련'}] {q}")
            rank = None
            for i, item in enumerate(res, 1):
                mark = ""
                if want is not None and item["contextId"] == want:
                    mark, rank = "  ← 의도", i
                print(f"    {i}. rec={item['recordId']} ctx={item['contextId']} "
                      f"sim={item['similarity']:.4f}  {label(item['contextId'])}{mark}")
            if res:
                (top1_unrelated if want is None else top1_related).append(res[0]["similarity"])
            if want is not None:
                print(f"    → 의도 순위 {rank}  "
                      f"[{'PASS' if rank and rank <= 3 else 'FAIL'} (상위3위 기준)]")
            else:
                over = [x for x in res if x["similarity"] >= SETTINGS.similarity_floor]
                print(f"    → 반환 {len(res)}건, {SETTINGS.similarity_floor} 이상 {len(over)}건")

        print()
        print("=" * 78)
        print("C. 분리도")
        print("=" * 78)
        print(f"  관련 top1: min={min(top1_related):.4f} max={max(top1_related):.4f} "
              f"avg={sum(top1_related)/len(top1_related):.4f}")
        print(f"  무관 top1: min={min(top1_unrelated):.4f} max={max(top1_unrelated):.4f} "
              f"avg={sum(top1_unrelated)/len(top1_unrelated):.4f}")
        print(f"  간격(관련 min − 무관 max) = {min(top1_related) - max(top1_unrelated):+.4f}")

        print()
        print("=" * 78)
        print("D. DISTINCT ON — record 5001은 ctx 1001/1007 두 건 보유")
        print("=" * 78)
        for q in ["친구들이랑 시끌벅적하게 놀 만한 곳", "사진 전시 구경"]:
            r = await client.post(url, headers=headers(),
                                  json={"userId": 9001, "query": q, "limit": 10,
                                        "embeddingProfile": PROFILE})
            hits = [x for x in r.json()["results"] if x["recordId"] == 5001]
            print(f"  '{q}'")
            if hits:
                print(f"    rec=5001 등장 {len(hits)}회 (1이어야 정상) → "
                      f"대표 ctx={hits[0]['contextId']} sim={hits[0]['similarity']:.4f}")
            else:
                print("    미등장")

        print()
        print("=" * 78)
        print("E. 사용자 격리 — ctx 1008은 user 9002 소유")
        print("=" * 78)
        q = "혼자 조용히 책 읽기 좋은 카페"
        r = await client.post(url, headers=headers(),
                              json={"userId": 9001, "query": q, "limit": 10,
                                    "embeddingProfile": PROFILE})
        ids = [x["contextId"] for x in r.json()["results"]]
        print(f"  user 9001 결과 ctx: {ids}")
        print(f"  → 1008 누출: {'있음 FAIL' if 1008 in ids else '없음 PASS'}")
        r = await client.post(url, headers=headers(),
                              json={"userId": 9002, "query": q, "limit": 10,
                                    "embeddingProfile": PROFILE})
        print(f"  user 9002 결과 ctx: {[x['contextId'] for x in r.json()['results']]}")


asyncio.run(main())

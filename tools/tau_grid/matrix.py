"""후보 유사도 행렬을 DB 에서 한 번 떠서 JSON 으로 굳힌다.

**τ 스윕이 GMS 를 부르지 않게 하는 것이 이 파일의 목적이다.**

τ 는 후보 선정에만 걸리고 임베딩에는 걸리지 않는다. 벡터는 τ 와 무관하게 고정이므로
`(Context, Preset)` 유사도 42×27 을 한 번 계산해 두면 임의의 τ 에 대한 후보 집합을
**LLM 호출 없이** 재구성할 수 있다. `tools/emb_grid` 가 조건마다 전량 재시딩하는 것과
다른 이유가 여기 있다 — 저쪽은 조건이 임베딩을 바꾸므로 다시 만들 수밖에 없다.

유사도 계산은 `app.service.keyword_service._topk` 를 **그대로 부른다.** 같은 식을 여기
다시 적으면 둘이 갈라지고, 갈라진 채 재면 우리가 고른 τ 가 서버가 쓰는 τ 가 아니게 된다.

    python tools/tau_grid/matrix.py            # .tau/matrix.json 생성
    python tools/tau_grid/matrix.py --out X    # 경로 지정

행렬 하나에 셋을 함께 담는다.

    sim         27개 프리셋 전부에 대한 유사도(후보 밖도 남긴다 — τ 를 내리는 쪽도 봐야 한다)
    rank        유사도 내림차순 순위. K 상한이 τ 와 별개로 후보를 자르는 것을 보이기 위함
    selected    현행 τ=0.30 판정에서 실제로 붙은 키워드. 오프라인 재구성의 기준점
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.cache.preset_cache import PresetCache  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repository import keyword_preset_repo  # noqa: E402
from app.service.keyword_service import _to_array  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    # T28. 콘솔이 cp949 면 본문 한 글자에 측정이 죽는다.
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


# 시연 정본은 15432 다. `ai/.env` 의 기본값은 07-27 잔재인 5433 이라 그대로 두면
# **데이터가 없는 DB 를 재고 「0건」을 결론으로 낸다**(T33).
EXPECT_PORT = "15432"

_CONTEXTS = """
SELECT ce.context_id, ce.embedding, c.body, c.member_id
FROM ai.context_embedding ce
JOIN core.context c ON c.id = ce.context_id
WHERE ce.embedding_profile = $1 AND c.deleted_at IS NULL
ORDER BY ce.context_id
"""

_SELECTED = """
SELECT context_id, keyword_id, confidence
FROM ai.context_keyword
ORDER BY context_id, keyword_id
"""


async def build(db: Database, profile: str) -> dict:
    cache = PresetCache()
    async with db.acquire() as conn:
        loaded = cache.load(await keyword_preset_repo.load_active(conn, profile))
        if not loaded:
            raise SystemExit(
                f"profile={profile} 로 적재된 활성 프리셋이 0건이다. 재지 않고 멈춘다."
            )
        ctx_rows = await conn.fetch(_CONTEXTS, profile)
        sel_rows = await conn.fetch(_SELECTED)

    if not ctx_rows:
        raise SystemExit(f"profile={profile} 인 Context 임베딩이 0건이다. 재지 않고 멈춘다.")

    presets = cache.snapshot().presets
    log(f"  프리셋 {loaded}개 · Context {len(ctx_rows)}건 · 현행 판정 {len(sel_rows)}행")

    selected: dict[int, dict[int, float | None]] = {}
    for r in sel_rows:
        conf = r["confidence"]
        selected.setdefault(r["context_id"], {})[r["keyword_id"]] = (
            float(conf) if conf is not None else None
        )

    # `_topk` 와 같은 식. 저쪽은 top-K 를 자른 뒤 floor 를 걸지만 여기서는 27개 전부를
    # 남기고 순위만 매긴다 — K 와 τ 중 무엇이 후보를 잘랐는지 스윕에서 갈라 보기 위함이다.
    mat = np.stack([p.embedding for p in presets])
    mat_norms = np.linalg.norm(mat, axis=1)
    mat_norms[mat_norms == 0] = 1.0

    contexts = []
    for row in ctx_rows:
        vec = _to_array(row["embedding"])
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            log(f"  [주의] context_id={row['context_id']} 벡터 norm=0 — 건너뛴다")
            continue
        sims = (mat @ (vec / norm)) / mat_norms
        order = np.argsort(-sims)
        chosen = selected.get(row["context_id"], {})
        contexts.append(
            {
                "context_id": row["context_id"],
                "member_id": row["member_id"],
                "body": row["body"],
                "candidates": [
                    {
                        "id": presets[i].id,
                        "code": presets[i].code,
                        "display_name": presets[i].display_name,
                        "category": presets[i].category,
                        "visibility": presets[i].visibility,
                        "sim": round(float(sims[i]), 6),
                        "rank": rank,
                        "selected": presets[i].id in chosen,
                        "confidence": chosen.get(presets[i].id),
                    }
                    for rank, i in enumerate(order, 1)
                ],
            }
        )

    orphan = sum(
        1
        for cid, kws in selected.items()
        if cid not in {c["context_id"] for c in contexts}
        for _ in kws
    )
    if orphan:
        # 현행 판정 행이 이 profile 의 Context 밖에 있으면 재구성의 기준점이 어긋난다.
        log(f"  [주의] 이 profile 밖 Context 의 판정 행이 {orphan}행 있다 — 재구성에서 빠진다")

    return {
        "profile": profile,
        "preset_count": loaded,
        "context_count": len(contexts),
        "selected_rows": sum(len(v) for v in selected.values()),
        "contexts": contexts,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / ".tau" / "matrix.json"))
    args = ap.parse_args()

    settings = get_settings()
    url = settings.database_url
    if EXPECT_PORT not in url:
        raise SystemExit(
            f"DATABASE_URL 이 :{EXPECT_PORT} 를 가리키지 않는다 — {url.rsplit('@', 1)[-1]}\n"
            f"시연 정본은 :{EXPECT_PORT}(pinlog-demo)다(T33). 재지 않고 멈춘다."
        )

    log(f"  profile={settings.embedding_profile}")
    db = Database(url)
    await db.connect()
    try:
        data = await build(db, settings.embedding_profile)
    finally:
        await db.disconnect()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  → {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

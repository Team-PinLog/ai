"""조건별 프리셋 임베딩을 떠서 유사도 행렬을 만든다. `S15P11A705-228`.

**이 파일이 `tau_grid/matrix.py` 와 갈리는 지점이 이 티켓의 대가다.** 저쪽은 DB 에 이미
있는 프리셋 벡터를 그대로 읽는다 — τ 는 임베딩을 바꾸지 않기 때문이다. `description`·
`examples` 는 바꾼다. `preset_embed_text` 가 둘을 다 포함하므로(`embedding_client.py:36`)
조건마다 **프리셋 27개를 다시 임베딩해야 한다.**

    비용    조건당 임베딩 27건 = API 호출 1회(_BATCH=128). Context 임베딩은 그대로다
    파급    후보 선정 결과가 달라진다 → `-210` 의 τ 분포가 무효가 된다(그래서 재검증한다)

`BD-18`(back) 이 *"프리셋 확장은 임베딩 재생성을 동반하므로 가벼운 작업이 아니다"* 로
적어 둔 것이 이것이다.

## DB 를 재시딩하지 않는다

조건이 4개인데 조건마다 `ai.keyword_preset` 을 갈아엎으면 (a) 시연 DB 가 측정 중간
상태로 남고 (b) 되돌리기가 측정의 일부가 된다. 대신 **벡터를 메모리에서만 만들고**
Context 임베딩만 DB 에서 읽는다. 판정 하네스(`run.py`)도 같은 파일을 읽으므로 DB 의
프리셋은 처음부터 끝까지 손대지 않는다.

## base 조건으로 경로를 검증한다

`base` 는 시드 yaml 그대로이므로 **DB 에 적재된 벡터와 같아야 한다.** 같지 않으면 이
하네스의 임베딩 경로가 부트스트랩의 그것과 어긋난 것이고, 그러면 조건 비교 전체가
무효다. 코사인을 실제로 대조하고 어긋나면 멈춘다.

    python tools/preset_desc/matrix.py --cond base D E DE
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.cache.preset_cache import PresetCache  # noqa: E402
from app.client.embedding_client import EmbeddingClient, preset_embed_text  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repository import keyword_preset_repo  # noqa: E402
from app.service.keyword_service import _to_array  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from variants import CONDITIONS, build  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    # T28. 콘솔이 cp949 면 본문 한 글자에 측정이 죽는다.
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


# T33 — `.env` 기본값 `:5433` 에는 데이터가 없다. 그대로 재면 「0건」이 결론이 된다.
EXPECT_PORT = "15432"

# base 벡터가 DB 적재분과 이만큼은 같아야 한다. 임베딩 API 는 완전 결정적이지 않아
# 부동소수 하위 자리가 흔들릴 수 있으므로 1.0 을 요구하지 않는다.
_BASE_COSINE_MIN = 0.9999

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


async def _load_db(db: Database, profile: str) -> tuple[list, list, dict]:
    cache = PresetCache()
    async with db.acquire() as conn:
        loaded = cache.load(await keyword_preset_repo.load_active(conn, profile))
        if not loaded:
            raise SystemExit(f"profile={profile} 활성 프리셋이 0건이다. 재지 않고 멈춘다.")
        ctx_rows = await conn.fetch(_CONTEXTS, profile)
        sel_rows = await conn.fetch(_SELECTED)
    if not ctx_rows:
        raise SystemExit(f"profile={profile} Context 임베딩이 0건이다. 재지 않고 멈춘다.")

    selected: dict[int, dict[int, float | None]] = {}
    for r in sel_rows:
        conf = r["confidence"]
        selected.setdefault(r["context_id"], {})[r["keyword_id"]] = (
            float(conf) if conf is not None else None
        )
    return list(ctx_rows), list(cache.snapshot().presets), selected


def _verify_base(presets_db: list, vectors: list[list[float]], order: list[dict]) -> None:
    """base 조건 벡터가 DB 적재분과 같은가 — 임베딩 경로의 동일성 검사."""
    by_id = {p.id: p.embedding for p in presets_db}
    worst = (1.0, None)
    for meta, raw in zip(order, vectors):
        db_vec = by_id.get(meta["id"])
        if db_vec is None:
            raise SystemExit(f"DB 에 없는 프리셋 id={meta['id']} ({meta['code']})")
        a = np.asarray(raw, dtype=np.float32)
        cos = float(a @ db_vec / (np.linalg.norm(a) * np.linalg.norm(db_vec)))
        if cos < worst[0]:
            worst = (cos, meta["code"])
    log(f"  base 검증 — DB 적재 벡터와의 코사인 최솟값 {worst[0]:.6f} ({worst[1]})")
    if worst[0] < _BASE_COSINE_MIN:
        raise SystemExit(
            f"base 벡터가 DB 적재분과 다르다(최소 코사인 {worst[0]:.6f} < {_BASE_COSINE_MIN}).\n"
            "이 하네스의 임베딩 경로가 부트스트랩과 어긋났다는 뜻이고, 그러면 조건 비교가\n"
            "전부 무효다. 재지 않고 멈춘다."
        )


def _build_matrix(
    cond: str, presets: list[dict], vectors: list[list[float]],
    ctx_rows: list, selected: dict, profile: str,
) -> dict:
    """`tau_grid/matrix.json` 과 **같은 스키마**로 낸다.

    같게 두는 이유는 `tau_grid/sweep.py`·`score.py` 를 그대로 재사용하기 위해서다.
    τ 재검증에 새 집계기를 짜면 `-210` 과 같은 자를 대고 있다는 보장이 사라진다.
    """
    mat = np.stack([np.asarray(v, dtype=np.float32) for v in vectors])
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
                        "id": presets[i]["id"],
                        "code": presets[i]["code"],
                        "display_name": presets[i]["display_name"],
                        "category": presets[i]["category"],
                        "visibility": presets[i].get("visibility", "PUBLIC"),
                        "sim": round(float(sims[i]), 6),
                        "rank": rank,
                        # **조건이 바뀌어도 현행 판정(base·DB) 그대로다.** 이 칸의 의미는
                        # 「지금 서비스가 붙여 둔 것」이고, τ 재구성의 기준점이 조건마다
                        # 달라지면 `-210` 과 비교가 끊긴다. 조건별 판정은 run.py 가 낸다.
                        "selected": presets[i]["id"] in chosen,
                        "confidence": chosen.get(presets[i]["id"]),
                    }
                    for rank, i in enumerate(order, 1)
                ],
            }
        )

    return {
        "profile": profile,
        "condition": cond,
        "preset_count": len(presets),
        "context_count": len(contexts),
        "selected_rows": sum(len(v) for v in selected.values()),
        "embed_texts": {p["code"]: preset_embed_text(p) for p in presets},
        "contexts": contexts,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cond", nargs="+", default=list(CONDITIONS))
    ap.add_argument("--outdir", default=str(ROOT / ".preset_desc"))
    args = ap.parse_args()

    settings = get_settings()
    if EXPECT_PORT not in settings.database_url:
        raise SystemExit(
            f"DATABASE_URL 이 :{EXPECT_PORT}(pinlog-demo)를 가리키지 않는다 — "
            f"{settings.database_url.rsplit('@', 1)[-1]} (T33). 재지 않고 멈춘다."
        )

    db = Database(settings.database_url)
    await db.connect()
    try:
        ctx_rows, presets_db, selected = await _load_db(db, settings.embedding_profile)
    finally:
        await db.disconnect()
    log(
        f"  profile={settings.embedding_profile} · DB 프리셋 {len(presets_db)}개 · "
        f"Context {len(ctx_rows)}건 · 현행 판정 {sum(len(v) for v in selected.values())}행"
    )

    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    for cond in args.cond:
        out = outdir / f"matrix-{cond}.json"
        if out.exists():
            # 재개. 임베딩은 결정적이지만 호출은 되돌릴 수 없다 — 이유 없이 다시 쓰지 않는다.
            log(f"  {cond}: 이미 있음 — 건너뛴다 ({out.name})")
            continue
        presets = build(cond)
        texts = [preset_embed_text(p) for p in presets]
        log(f"\n  [{cond}] 프리셋 {len(texts)}건 임베딩 …")
        vectors = await client.embed(texts)
        if cond == "base":
            _verify_base(presets_db, vectors, presets)

        data = _build_matrix(
            cond, presets, vectors, ctx_rows, selected, settings.embedding_profile
        )
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        top1 = [c["candidates"][0]["sim"] for c in data["contexts"]]
        n_cand = [
            sum(1 for x in c["candidates"] if x["rank"] <= 10 and x["sim"] >= 0.30)
            for c in data["contexts"]
        ]
        zero = sum(1 for x in n_cand if x == 0)
        log(
            f"  {cond}: top-1 min {min(top1):.4f} · τ=0.30·k=10 후보 평균 "
            f"{sum(n_cand) / len(n_cand):.2f} · 후보 0개 Context {zero}건"
        )
        log(f"       → {out}  ({out.stat().st_size:,} bytes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

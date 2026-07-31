"""무관한 본문의 top-1 유사도를 재서 Context 게이트 γ 의 근거를 넓힌다.

실데이터 42건에서 「판정을 하지 말았어야 할 Context」는 2건뿐이고(둘 다 같은 본문),
그 2건에 맞춰 γ 를 고르면 **표본 둘에 맞춘 값**이 된다. 게이트가 무엇을 막으라고
있는 것인지 — 프리셋 어디에도 걸리지 않는 입력 — 를 직접 만들어 재면 근거가 넓어진다.

**임베딩만 부른다. 판정(GMS LLM)은 부르지 않는다.** top-1 유사도는 벡터끼리의 값이라
LLM 이 필요 없다. 프로브 한 줄에 임베딩 1회, 건당 40토큰 남짓이다.

    python tools/tau_grid/probe_gate.py

두 묶음을 넣는다. 게이트는 **둘 사이를 갈라야** 쓸 수 있다.

    irrelevant   프리셋 27개 어디에도 해당하지 않는 방문 기록. γ 아래에 있어야 한다
    terse        짧지만 정상인 기록. γ 위에 있어야 한다 — 여기가 잘리면 게이트가 해롭다
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
from app.client.embedding_client import EmbeddingClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repository import keyword_preset_repo  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


# 프리셋 27개(COMPANION·ACTIVITY·ATMOSPHERE·SITUATION) 어디에도 걸리지 않는 방문 기록.
# 「장소에 갔다」는 형식은 실데이터와 같게 두었다 — 형식이 달라서 유사도가 낮은 것이면
# 게이트가 무관함이 아니라 문체를 재는 것이 된다.
IRRELEVANT = [
    "엔진오일 교환하고 타이어 공기압도 봐달라고 했다",
    "스케일링 받았는데 잇몸에서 피가 좀 났다",
    "휴대폰 액정 깨져서 수리 맡겼고 두 시간 걸린다고 함",
    "겨울 코트 세탁 맡김. 다음 주 수요일에 찾으러 가야 한다",
    "전세 대출 상담받았다. 서류 두 개 더 떼오라고 했음",
    "정기 검진 받으러 감. 채혈하고 결과는 다음 주에 나온다",
    "머리 다듬고 염색 뿌리만 손봤다",
    "택배 반품 접수하러 들렀음",
]

# 짧지만 정상인 기록. 실데이터의 짧은 본문(「가지튀김이 미쳤음」)과 길이는 같고
# **프리셋에 걸릴 근거는 있는** 것들이다. 게이트가 이쪽을 자르면 도입하면 안 된다.
TERSE = [
    "돈까스 맛있음",
    "여기 커피 진하다",
    "회 먹었는데 신선했음",
    "티라미수 최고",
    "밤에 야경 보임",
    "혼자 앉아 있기 좋음",
    "친구랑 왔음",
    "소주 한잔 했다",
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / ".tau" / "probe.json"))
    args = ap.parse_args()

    settings = get_settings()
    if "15432" not in settings.database_url:
        raise SystemExit("DATABASE_URL 이 :15432 가 아니다(T33). 재지 않고 멈춘다.")

    db = Database(settings.database_url)
    await db.connect()
    cache = PresetCache()
    try:
        async with db.acquire() as conn:
            n = cache.load(
                await keyword_preset_repo.load_active(conn, settings.embedding_profile)
            )
    finally:
        await db.disconnect()
    if not n:
        raise SystemExit("활성 프리셋이 0건이다. 재지 않고 멈춘다.")

    presets = cache.snapshot().presets
    mat = np.stack([p.embedding for p in presets])
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0] = 1.0

    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    groups = {"irrelevant": IRRELEVANT, "terse": TERSE}
    result: dict[str, list[dict]] = {}

    for name, texts in groups.items():
        log(f"\n  [{name}] {len(texts)}건")
        rows = []
        # 배치로 한 번에 부른다. 건별로 부르면 호출 수만 늘고 재는 값은 같다.
        vectors = await client.embed(texts)
        for text, raw in zip(texts, vectors):
            vec = np.asarray(raw, dtype=np.float32)
            vec = vec / float(np.linalg.norm(vec))
            sims = (mat @ vec) / norms
            order = np.argsort(-sims)
            top = [
                {"code": presets[i].code, "sim": round(float(sims[i]), 4)}
                for i in order[:3]
            ]
            rows.append({"text": text, "top1": top[0]["sim"], "top3": top})
            log(
                f"    {top[0]['sim']:.4f}  {text[:32]:<34} "
                + " ".join(f"{t['code']}={t['sim']:.3f}" for t in top)
            )
        result[name] = rows

    log("\n" + "=" * 78)
    log("게이트가 두 묶음을 가르는가")
    log("=" * 78)
    irr = np.array([r["top1"] for r in result["irrelevant"]])
    ter = np.array([r["top1"] for r in result["terse"]])
    log(f"  irrelevant  n={irr.size}  min={irr.min():.4f} p50={np.median(irr):.4f} max={irr.max():.4f}")
    log(f"  terse       n={ter.size}  min={ter.min():.4f} p50={np.median(ter):.4f} max={ter.max():.4f}")
    log()
    if irr.max() < ter.min():
        log(f"  → 갈린다. γ 는 ({irr.max():.4f}, {ter.min():.4f}) 사이에 둘 수 있다")
    else:
        # 겹치면 게이트만으로는 무관 입력을 가려낼 수 없다. 그 사실이 결론이다.
        log(f"  → 겹친다. irrelevant 최댓값 {irr.max():.4f} ≥ terse 최솟값 {ter.min():.4f}")
        log("     γ 를 어디에 두든 무관 입력이 통과하거나 정상 입력이 잘린다")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""테스트 A — 프리셋 자기 중복.

27개 프리셋을 임베딩해 쌍별 cosine을 계산하고, 임계 이상인 쌍을
'병합 후보'로 보고한다. 프리셋끼리 너무 비슷하면 후보 검색이 서로를
밀어내고 LLM 판정도 흔들린다.

사용: python test_a_selfdup.py [--threshold 0.9]
"""
from __future__ import annotations

import argparse

import numpy as np

from embed import cosine_matrix, embed, load_presets, preset_embed_text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.9)
    args = ap.parse_args()

    presets = load_presets()
    texts = [preset_embed_text(p) for p in presets]
    vecs = embed(texts)
    sim = cosine_matrix(vecs, vecs)

    n = len(presets)
    iu = np.triu_indices(n, k=1)
    pairs = sorted(
        ((sim[i, j], i, j) for i, j in zip(*iu)), key=lambda x: -x[0]
    )

    print(f"# 테스트 A — 프리셋 자기 중복 (n={n}, threshold={args.threshold})\n")

    dup = [(s, i, j) for s, i, j in pairs if s >= args.threshold]
    print(f"## 병합 후보 (cosine ≥ {args.threshold}): {len(dup)}건")
    if dup:
        for s, i, j in dup:
            print(f"  {s:.3f}  {presets[i]['code']:<16} ↔ {presets[j]['code']}")
    else:
        print("  없음 — 프리셋 독립성 OK")

    print("\n## 프리셋별 최근접 이웃 (상위 참고)")
    off = sim.copy()
    np.fill_diagonal(off, -1)
    nn = off.argmax(axis=1)
    rows = sorted(range(n), key=lambda i: -off[i, nn[i]])
    for i in rows[:10]:
        j = nn[i]
        print(f"  {off[i, j]:.3f}  {presets[i]['code']:<16} → {presets[j]['code']}")

    upper = sim[iu]
    print("\n## off-diagonal 분포 (후보 하한 보정 참고)")
    for q in (50, 75, 90, 95, 99):
        print(f"  p{q}: {np.percentile(upper, q):.3f}")
    print(f"  max: {upper.max():.3f}  mean: {upper.mean():.3f}")


if __name__ == "__main__":
    main()

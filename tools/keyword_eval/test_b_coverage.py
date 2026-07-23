"""테스트 B — 커버리지.

샘플 맥락을 임베딩해 각 샘플의 top-K 프리셋을 뽑고, 프리셋 세트가
실제 입력을 얼마나 덮는지 분포로 본다. 지상진실 라벨이 없으므로
'분포 건전성' 지표 중심이다(임시 샘플이라 수치는 경향으로 해석).

지표:
  - 미매칭율   : top-1 cosine < 하한인 샘플 비율
  - 쏠림도     : top-K 등장 프리셋의 max-share / Gini
  - 사각지대   : 어느 샘플의 top-K에도 안 나오는 프리셋
  - Recall@K   : 샘플의 expect(의도 프리셋 code)가 top-K에 있는 비율(있을 때만)
  - 하한 제안  : top-1 점수 분포로 후보 하한 후보값 산출

사용: python test_b_coverage.py [--k 10] [--floor 0.30] [--samples samples.yaml]
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import yaml

from embed import cosine_matrix, embed, load_presets, preset_embed_text

HERE = Path(__file__).resolve().parent


def gini(xs: np.ndarray) -> float:
    if xs.sum() == 0:
        return 0.0
    x = np.sort(xs.astype(float))
    n = len(x)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(x) / (n * x.sum()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--floor", type=float, default=0.30)
    ap.add_argument("--samples", default=str(HERE / "samples.yaml"))
    args = ap.parse_args()

    presets = load_presets()
    codes = [p["code"] for p in presets]
    pv = embed([preset_embed_text(p) for p in presets])

    doc = yaml.safe_load(Path(args.samples).read_text(encoding="utf-8"))
    samples = doc["samples"]
    sv = embed([s["text"] for s in samples])

    sim = cosine_matrix(sv, pv)  # (n_samples, n_presets)
    K = args.k

    top1 = sim.max(axis=1)
    unmatched = float((top1 < args.floor).mean())

    appear = Counter()
    recall_hit = recall_total = 0
    for si, s in enumerate(samples):
        order = np.argsort(-sim[si])[:K]
        for pi in order:
            if sim[si, pi] >= args.floor:
                appear[codes[pi]] += 1
        expect = s.get("expect") or []
        if expect:
            topk_codes = {codes[pi] for pi in order}
            recall_total += 1
            if any(e in topk_codes for e in expect):
                recall_hit += 1

    counts = np.array([appear.get(c, 0) for c in codes], dtype=float)
    total = counts.sum()
    blind = [c for c in codes if appear.get(c, 0) == 0]

    print(f"# 테스트 B — 커버리지 (samples={len(samples)}, K={K}, floor={args.floor})\n")
    print(f"미매칭율 (top1<{args.floor}): {unmatched:.1%}")
    if total:
        share = counts / total
        print(f"쏠림도  : max-share {share.max():.1%}  Gini {gini(counts):.3f}")
    if recall_total:
        print(f"Recall@{K} (expect 라벨 기준, 편향·참고용): {recall_hit}/{recall_total} = {recall_hit/recall_total:.1%}")
    print(f"사각지대 (top-{K} 미등장 프리셋 {len(blind)}개): {', '.join(blind) or '없음'}")

    print("\n## top-1 점수 분포 (후보 하한 제안 참고)")
    for q in (10, 25, 50, 75, 90):
        print(f"  p{q}: {np.percentile(top1, q):.3f}")
    print(f"  min {top1.min():.3f}  mean {top1.mean():.3f}  max {top1.max():.3f}")

    print("\n## 프리셋별 등장 횟수 (top-K, floor 통과)")
    for c in sorted(codes, key=lambda c: -appear.get(c, 0)):
        print(f"  {appear.get(c,0):>3}  {c}")


if __name__ == "__main__":
    main()

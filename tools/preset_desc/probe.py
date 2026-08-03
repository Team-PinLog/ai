"""라벨 42건 **밖의** 본문으로 개정을 검증한다. `S15P11A705-228`. 임베딩만 부른다.

## 이 파일이 막으려는 것

계약이 지목한 위험이다 — 라벨 42건을 보고 `description` 을 고치면 그 42건에서만
좋아진다. `variants.py` 는 수정 내용을 라벨과 무관한 원칙으로 정해 그 위험을 줄였고,
`split_score.py` 는 고치지 않은 22종을 대조군으로 뒀다. **셋째 방어가 이 파일이다 —
42건에 없는 입력을 새로 만들어 잰다.**

`-210` 의 `probe_gate.py` 와 같은 수법이다. 저쪽은 게이트 γ 를 표본 2건에 맞추는 것을
막으려고 무관 입력을 직접 만들었다.

## 두 묶음이 반대 방향을 예측한다

개정 원칙은 「다른 프리셋 소관 어휘를 걷는다」였다. 그 원칙이 참이면 이렇게 갈려야 한다.

    cross    걷어낸 어휘 영역에 있으나 그 키워드의 근거는 없는 본문   유사도가 **내려간다**
    direct   그 키워드의 근거가 본문에 있는 본문                    유사도가 **유지된다**

**한 방향만 재면 안 된다.** cross 만 내려가면 「좁혔다」가 아니라 「전부 낮췄다」일 수
있고, 그것은 붙어야 할 것도 안 붙는다는 뜻이다. 좁히기의 대가가 direct 에 나타난다.

## 이 프로브의 한계

문장을 이 티켓의 작업자가 썼고, **그 작업자가 개정안을 설계한 사람이기도 하다.**
개정 원칙이 겨눈 어휘를 알고 있으므로 cross 문장이 그쪽으로 기울었을 수 있다. 42건과
독립이라는 것까지가 이 프로브가 보증하는 것이고, 편향으로부터 독립이라는 뜻은 아니다.

문장은 **개정안을 확정한 뒤에** 썼다. 프로브를 먼저 쓰고 거기 맞춰 개정하면 그것이
바로 자기충족이다.

    python tools/preset_desc/probe.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.client.embedding_client import EmbeddingClient, preset_embed_text  # noqa: E402
from app.core.config import get_settings  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from variants import CONDITIONS, TARGETS, build  # noqa: E402

for _s in (sys.stdout, sys.stderr):
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


# ── cross — 걷어낸 어휘 영역에 있으나 해당 키워드의 근거는 **없는** 본문 ──────────
#
# 각 문장은 개정 전 텍스트가 그 키워드로 끌어당기던 어휘 영역에 있다. 그 영역이
# 실제로는 다른 프리셋 소관이므로 **개정 후 유사도가 내려가야 한다.**
CROSS: dict[str, list[str]] = {
    # 걷어낸 것: examples 의 「안주」 · description 의 「모임」.
    # 음식 평가만 있고 음주 언급이 없는 문장이다.
    "DRINK": [
        "튀김이 바삭해서 계속 손이 갔다",
        "곱창이 두툼하고 불맛 제대로였음",
        "다섯이 모여서 이것저것 시켜 먹었다",
    ],
    # 걷어낸 것: examples 의 「밥 먹고」 · description 의 「둘러보는」.
    # 식사 기록과 실내 구경 기록이다. 걷는 활동이 없다.
    "WALK": [
        "점심으로 국수 먹고 근처 카페 갔다",
        "안쪽 자리에 앉아서 메뉴판 천천히 봤음",
        "매장 구경하다가 결국 하나 골랐다",
    ],
    # 걷어낸 것: examples 의 「저녁 먹었다」·「분위기」 · description 의 「외출」.
    # 식사·분위기 언급은 있으나 가족 동행이 없다.
    "WITH_FAMILY": [
        "주말 저녁으로 삼겹살 구워 먹었다",
        "어른들 오기 편할 것 같은 자리 배치",
        "다 같이 나들이 나온 김에 들렀음",
    ],
    # 걷어낸 것: examples 의 「한잔」·「탁 트인」 · description 의 「좋은 곳」.
    # 지형·개방감·일반 호평이다. 보이는 경치가 없다.
    "VIEW_GOOD": [
        "언덕 위라 올라오기가 좀 힘들었다",
        "천장이 높고 자리 간격도 널찍했음",
        "여기 진짜 좋았다 또 오고 싶음",
    ],
    # 걷어낸 것: description 의 「분위기 있는」.
    # 분위기 일반·업종 언급이다. 사진·인테리어가 없다.
    "TRENDY": [
        "브런치 카페인데 리코타 샌드위치 시켰다",
        "분위기가 나쁘지 않았음",
        "조명이 은은해서 마음이 편해졌다",
    ],
}

# ── direct — 그 키워드의 근거가 본문에 **있는** 본문 ─────────────────────────
#
# 좁히기의 대가를 잰다. 여기가 함께 내려가면 붙어야 할 것도 안 붙게 된다는 뜻이다.
DIRECT: dict[str, list[str]] = {
    "DRINK": [
        "생맥주 두 잔씩 하고 나왔다",
        "여기 막걸리가 진짜 잘 넘어감",
        "늦게까지 앉아서 계속 따라 마셨다",
    ],
    "WALK": [
        "천변 따라 삼십 분쯤 걸었다",
        "공원 한 바퀴 돌고 벤치에 앉았음",
        "바람 좋아서 계속 돌아다녔다",
    ],
    "WITH_FAMILY": [
        "엄마 아빠랑 셋이 다녀왔다",
        "형이랑 오랜만에 만나서 같이 감",
        "명절에 온 식구가 모였던 곳",
    ],
    "VIEW_GOOD": [
        "창가에서 강이 다 내려다보인다",
        "밤에 불빛 깔린 게 진짜 예뻤음",
        "자리에 앉으면 산등성이가 쭉 보인다",
    ],
    "TRENDY": [
        "벽 타일이랑 조명이 다 사진각이었다",
        "인테리어 소품이 하나하나 신경 쓴 티",
        "여기서 찍은 사진 다 잘 나옴",
    ],
}


def _flat(groups: dict[str, list[str]]) -> list[tuple[str, str]]:
    return [(code, t) for code in TARGETS for t in groups[code]]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cond", nargs="+", default=[c for c in CONDITIONS if c != "base2"])
    ap.add_argument("--out", default=str(ROOT / ".preset_desc" / "probe.json"))
    args = ap.parse_args()

    settings = get_settings()
    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )

    items = [("cross", c, t) for c, t in _flat(CROSS)]
    items += [("direct", c, t) for c, t in _flat(DIRECT)]
    log(f"  프로브 {len(items)}건 (cross {len(_flat(CROSS))} · direct {len(_flat(DIRECT))})")
    probe_vecs = await client.embed([t for _, _, t in items])
    probe_mat = np.stack([np.asarray(v, dtype=np.float32) for v in probe_vecs])
    probe_mat /= np.linalg.norm(probe_mat, axis=1, keepdims=True)

    # 조건별 프리셋 벡터를 **다시 뜬다.** `matrix-*.json` 은 유사도만 담고 프리셋
    # 벡터는 안 담기 때문이다. 그래서 이 프로브의 벡터는 판정 회차가 쓴 것과 미세하게
    # 다르다(T68 — 같은 텍스트라도 매번 갈린다). 그 폭은 실측 최대 `0.0044` 이고
    # 아래에서 보는 조건 간 차이보다 한 자리 작으므로 결론을 바꾸지 않는다.
    per_cond: dict[str, dict[str, np.ndarray]] = {}
    for cond in args.cond:
        presets = build(cond)
        texts = [preset_embed_text(p) for p in presets]
        log(f"  [{cond}] 프리셋 {len(texts)}건 임베딩 …")
        vecs = await client.embed(texts)
        arr = np.stack([np.asarray(v, dtype=np.float32) for v in vecs])
        arr /= np.linalg.norm(arr, axis=1, keepdims=True)
        per_cond[cond] = {p["code"]: arr[i] for i, p in enumerate(presets)}

    rows = []
    for (group, code, text), pv in zip(items, probe_mat):
        row = {"group": group, "code": code, "text": text}
        for cond in args.cond:
            row[cond] = round(float(pv @ per_cond[cond][code]), 6)
        rows.append(row)

    base = args.cond[0]
    for group, title, want in (
        ("cross", "cross — 근거 없는 본문. **내려가야 한다**", "down"),
        ("direct", "direct — 근거 있는 본문. **유지되어야 한다**", "hold"),
    ):
        head(title)
        log(f"  {'프리셋':<13} {'본문':<32} " + " ".join(f"{c:>8}" for c in args.cond))
        log("  " + "─" * (46 + 9 * len(args.cond)))
        for r in [x for x in rows if x["group"] == group]:
            log(
                f"  {r['code']:<13} {r['text'][:30]:<32} "
                + " ".join(f"{r[c]:>8.4f}" for c in args.cond)
            )
        log()
        log(f"  {'프리셋':<13} " + " ".join(f"{'Δ' + c:>9}" for c in args.cond[1:]))
        for code in TARGETS:
            sub = [x for x in rows if x["group"] == group and x["code"] == code]
            b = st.mean(x[base] for x in sub)
            log(
                f"  {code:<13} "
                + " ".join(f"{st.mean(x[c] for x in sub) - b:>+9.4f}" for c in args.cond[1:])
            )
        allsub = [x for x in rows if x["group"] == group]
        b = st.mean(x[base] for x in allsub)
        log(
            f"  {'합계 평균':<13} "
            + " ".join(
                f"{st.mean(x[c] for x in allsub) - b:>+9.4f}" for c in args.cond[1:]
            )
        )
        log(f"  (기준 {base} 평균 {b:.4f} · 기대 방향 {want})")

    head("판독 — 좁혔는가, 전부 낮췄는가")
    for cond in args.cond[1:]:
        cr = [x for x in rows if x["group"] == "cross"]
        di = [x for x in rows if x["group"] == "direct"]
        dc = st.mean(x[cond] for x in cr) - st.mean(x[base] for x in cr)
        dd = st.mean(x[cond] for x in di) - st.mean(x[base] for x in di)
        verdict = (
            "좁혔다 (근거 없는 쪽만 내려간다)" if dc < 0 <= dd
            else "전부 낮췄다 (근거 있는 쪽도 함께 내려간다)" if dc < 0 and dd < 0
            else "안 내려갔다"
        )
        log(
            f"  {cond:<6} cross {dc:+.4f} · direct {dd:+.4f} · "
            f"벌어진 폭 {dd - dc:+.4f}  → {verdict}"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"conditions": args.cond, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

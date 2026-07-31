"""GMS 게이트웨이의 판정 쿼터가 RPM 인지 TPM 인지 가려낸다.

왜 필요한가: 2026-07-29 실측에서 판정이 분당 약 2건만 통과한다는 것은 알았지만
**무엇에 걸리는지는 몰랐다**(T27). 원인에 따라 대책이 정반대다.

    RPM 제한이면   프롬프트를 줄여도 소용없다 → 배치 판정으로 호출 수를 줄여야 한다
    TPM 제한이면   호출 수는 그대로 두고 후보 목록을 줄이면 통과율이 오른다

세 조건을 같은 방식으로 던져 비교한다.

    full   현행과 같은 크기 (후보 27개, prompt 약 790 토큰)
    slim   후보 5개로 줄인 것 (prompt 약 200 토큰)
    tiny   후보 없이 최소 (prompt 약 40 토큰)

같은 간격으로 같은 횟수를 던졌을 때 slim·tiny 의 성공률이 full 보다 높으면 TPM,
셋이 비슷하면 RPM 이다.

사용:
    python tools/keyword_eval/probe_quota.py                 # 세 조건 × 8회, 5초 간격
    python tools/keyword_eval/probe_quota.py --n 12 --gap 3
    python tools/keyword_eval/probe_quota.py --model gemini-2.0-flash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx  # noqa: E402

from app.core.config import get_settings  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_CONTEXT = "비 오는 날 우산 접고 들어갔는데 나무 창틀이랑 옛날 다방 같은 분위기가 좋았다."

_ALL_CANDIDATES = [
    ("RAINY_DAY", "비 오는 날"), ("COZY", "아늑한"), ("RETRO", "복고풍"),
    ("QUIET", "조용한"), ("ALONE", "혼자"), ("WITH_FRIENDS", "친구와"),
    ("WITH_PARTNER", "연인과"), ("WITH_FAMILY", "가족과"), ("MEAL", "식사"),
    ("DESSERT", "디저트"), ("DRINK", "술"), ("WALK", "산책"),
    ("STUDY_WORK", "작업"), ("VIEW_GOOD", "전망"), ("LIVELY", "활기찬"),
    ("SPACIOUS", "넓은"), ("GATHERING", "모임"), ("DATE_COURSE", "데이트"),
    ("ANNIVERSARY", "기념일"), ("TRENDY", "요즘"), ("EXHIBITION", "전시"),
    ("SHOPPING", "쇼핑"), ("QUICK_STOP", "잠깐"), ("LATE_NIGHT", "심야"),
    ("PET_FRIENDLY", "반려동물"), ("KID_FRIENDLY", "아이와"), ("PARKING", "주차"),
]

_SCHEMA = {
    "type": "object",
    "properties": {"selected": {"type": "array", "items": {"type": "string"}}},
    "required": ["selected"],
}


def build_body(n_candidates: int) -> dict:
    cands = _ALL_CANDIDATES[:n_candidates]
    lines = "\n".join(f"- {c}: {d}" for c, d in cands)
    user = f"[Context]\n{_CONTEXT}\n\n[후보]\n{lines}" if cands else f"[Context]\n{_CONTEXT}"
    return {
        "systemInstruction": {"parts": [{"text": "후보 중 맞는 것의 code 를 고른다."}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _SCHEMA,
            "maxOutputTokens": 512,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }


async def run_condition(
    client: httpx.AsyncClient, url: str, key: str, label: str, n_cand: int, n: int, gap: float
) -> dict:
    body = build_body(n_cand)
    ok = fail = 0
    prompt_tokens = 0
    latencies: list[float] = []
    codes: list[int] = []

    print(f"\n[{label}]  후보 {n_cand}개 · {n}회 · 간격 {gap}s")
    for i in range(n):
        t0 = time.monotonic()
        try:
            r = await client.post(
                url,
                headers={"x-goog-api-key": key, "content-type": "application/json"},
                json=body,
            )
            dt = time.monotonic() - t0
            codes.append(r.status_code)
            if r.status_code == 200:
                ok += 1
                latencies.append(dt)
                u = (r.json().get("usageMetadata") or {})
                prompt_tokens = u.get("promptTokenCount") or prompt_tokens
                print(f"  {i + 1:>2}. 200  {dt:>5.2f}s  prompt={u.get('promptTokenCount')}")
            else:
                fail += 1
                print(f"  {i + 1:>2}. {r.status_code}  {dt:>5.2f}s  {r.text[:80]}")
        except httpx.HTTPError as exc:
            fail += 1
            codes.append(0)
            print(f"  {i + 1:>2}. ERR  {exc}")
        if i < n - 1:
            await asyncio.sleep(gap)

    avg = sum(latencies) / len(latencies) if latencies else 0.0
    return {
        "label": label,
        "candidates": n_cand,
        "prompt_tokens": prompt_tokens,
        "ok": ok,
        "fail": fail,
        "rate": ok / n,
        "avg_latency": avg,
        "codes": codes,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="조건당 호출 수")
    ap.add_argument("--gap", type=float, default=5.0, help="호출 간격(초)")
    ap.add_argument(
        "--model", default=None, help="기본값은 .env 의 PINLOG_JUDGE_CHAIN 1순위 모델"
    )
    args = ap.parse_args()

    s = get_settings()
    model = args.model or s.judge_model
    root = s.gms_base_url.split("/gmsapi/")[0] + "/gmsapi"
    url = f"{root}/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    print("=" * 72)
    print(f"GMS 판정 쿼터 진단  model={model}  n={args.n}  gap={args.gap}s")
    print("=" * 72)

    results = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for label, n_cand in (("full", 27), ("slim", 5), ("tiny", 0)):
            results.append(
                await run_condition(client, url, s.gms_api_key, label, n_cand, args.n, args.gap)
            )
            # 조건 사이에 쿼터가 회복되도록 한 텀 쉰다. 이걸 빼면 앞 조건의
            # 소진이 뒤 조건 결과로 새어 비교가 무의미해진다.
            print("  (쿼터 회복 대기 60s)")
            await asyncio.sleep(60)

    print("\n" + "=" * 72)
    print(f"{'조건':<8}{'후보':>5}{'prompt':>9}{'성공':>6}{'실패':>6}{'성공률':>8}{'평균응답':>10}")
    for r in results:
        print(
            f"{r['label']:<8}{r['candidates']:>5}{r['prompt_tokens']:>9}"
            f"{r['ok']:>6}{r['fail']:>6}{r['rate'] * 100:>7.0f}%{r['avg_latency']:>9.2f}s"
        )

    full, slim, tiny = results
    print("\n판정:")
    if tiny["rate"] > full["rate"] + 0.25:
        print("  TPM 계열 — 프롬프트를 줄이면 통과율이 오른다. 후보 축소가 유효하다.")
    elif abs(tiny["rate"] - full["rate"]) <= 0.25:
        print("  RPM 계열 — 프롬프트 크기와 무관하다. 호출 수 자체를 줄여야 한다(배치 판정).")
    else:
        print("  판단 보류 — 표본이 작거나 쿼터가 시간에 따라 흔들린다. --n 을 늘려 재측정하라.")
    print(f"  응답 지연 자체는 평균 {full['avg_latency']:.2f}s — 429가 없다면 이것이 실사용 체감이다.")

    out = ".demo/quota-probe.json"
    os.makedirs(".demo", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"model": model, "n": args.n, "gap": args.gap, "results": results}, f,
                  ensure_ascii=False, indent=2)
    print(f"\n원본: {out}")
    return 0


sys.exit(asyncio.run(main()))

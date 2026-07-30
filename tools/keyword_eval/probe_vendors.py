"""같은 판정 작업을 벤더별로 던져 속도·토큰·결과 일치도를 비교한다.

왜 필요한가: GMS 는 SSAFY 공용 게이트웨이라 쿼터가 시점 의존이다(T27 정정본).
2026-07-30 Gemini 경로가 어제는 분당 2건, 오늘은 분당 30건 이상을 통과시켰다.
**한 프로바이더에 묶여 있으면 그 변동을 흡수할 방법이 없다.**

여기서 재는 것은 셋이다.

    속도    응답 지연과 429 발생 — 시연 당일 어느 경로가 살아 있는지
    비용    prompt·output 토큰 — 구조화 출력 방식이 달라 같지 않다
    일치도  같은 Context 에 같은 Keyword 를 고르는가 — 갈아탈 수 있는지의 조건

일치도가 낮으면 속도가 빨라도 갈아탈 수 없다. Keyword 는 사용자에게 보이는 값이고
프로파일·피드 점수의 입력이라, 벤더를 바꾸면 결과가 바뀐다.

사용:
    python tools/keyword_eval/probe_vendors.py            # 벤더별 5회
    python tools/keyword_eval/probe_vendors.py --n 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 측정 대상 Context. 성격이 서로 다른 것을 고른다 — 분위기 서술, 목적 서술,
# 사람 관계 서술, 부정 평가, 사실 나열. 한 종류만 재면 "이 Context 에서만"이 된다.
CONTEXTS = [
    ("demo-seed-host-rain",
     "비 오는 날 우산 접고 들어갔는데 나무 창틀이랑 옛날 다방 같은 분위기가 딱 좋았다. "
     "빗소리 들으면서 앉아 있기 아늑한 곳"),
    ("demo-seed-host-work",
     "혼자 노트북 들고 와서 세 시간 내리 작업했다. 말소리가 거의 없고 콘센트도 많아서 집중이 잘 됐다"),
    ("demo-seed-jeongheon-neworder",
     "2025년에 신한 친구들이랑 피맥했고, 2026년 재영이형, 형순이형, 덕이랑 같이 가서 피맥함"),
    ("demo-seed-jeongheon-justtendon",
     "여기 느끼해서 다신 안감. 다른 지점 갈 것"),
    ("demo-seed-gahyeon-okeeer",
     "책을 사면 꽃을 주는 서점. 서점지기님이 너무 친절하심"),
]

_PLACE_KEY = CONTEXTS[0][0]
CONTEXT = CONTEXTS[0][1]

SYSTEM = (
    "너는 장소 기록에 어울리는 Keyword 를 고르는 분류기다. "
    "후보 중 Context 가 실제로 담고 있는 것만 고른다. 애매하면 고르지 않는다."
)


def build_user(context_text: str, candidates: list[dict]) -> str:
    lines = [
        f"- id={p['id']} | {p['display_name']} ({p['category']}) | "
        f"의미: {p['description']} | 예: {' · '.join(p.get('examples') or [])}"
        for p in candidates
    ]
    return f"[Context]\n{CONTEXT}\n\n[후보 Keyword]\n" + "\n".join(lines)


async def load_candidates(place_key: str, limit: int) -> list[dict]:
    """실제 서비스와 같은 방식으로 후보를 뽑는다 — **벡터 유사도 top-K**.

    `ORDER BY id LIMIT 10` 으로 뽑으면 안 된다. 그것은 Context 와 무관한 임의
    10개라, 모델이 "고를 것이 없어서" 적게 고른 것인지 "판정이 보수적"이어서
    적게 고른 것인지 구별할 수 없다. 초안이 그 실수를 했고 일치도 비교가
    무의미해졌다(2026-07-30).

    Context 임베딩은 시딩된 것을 재사용한다 — 프로브가 임베딩을 새로 만들면
    그것 자체가 GMS 호출이 되어 측정을 흐린다.
    """
    s = get_settings()
    db = Database(s.database_url)
    await db.connect()
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT ce.embedding FROM ai.context_embedding ce "
                "JOIN core.context c ON c.id = ce.context_id "
                "JOIN core.record r ON r.id = c.record_id "
                "JOIN core.place p ON p.id = r.place_id "
                "WHERE p.kakao_place_id = $1 AND NOT ce.is_deleted LIMIT 1",
                place_key,
            )
            if row is None:
                raise SystemExit(
                    "기준 Context 임베딩이 없다. 먼저 seed.py 를 돌려라 "
                    f"(kakao_place_id = {place_key})"
                )
            rows = await conn.fetch(
                "SELECT id, display_name, category, description, examples, "
                "       1 - (embedding <=> $1) AS sim "
                "FROM ai.keyword_preset "
                "WHERE is_active AND embedding IS NOT NULL "
                "ORDER BY embedding <=> $1 LIMIT $2",
                row["embedding"], limit,
            )
        out = [dict(r) for r in rows]
        print("  후보(유사도 top-K):")
        for r in out:
            print(f"    id={r['id']:<5} {r['display_name']:<12} sim={r['sim']:.4f}")
        return out
    finally:
        await db.disconnect()


# ── 벤더별 요청·응답 어댑터 ────────────────────────────────────────────────
# 구조화 출력 방식이 셋 다 다르다. 그 차이가 output 토큰과 실패 양상을 가른다.

def req_gemini(root, key, model, user):
    return (
        f"{root}/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": key, "content-type": "application/json"},
        {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "object",
                    "properties": {
                        "selected": {
                            "type": "array",
                            "items": {"type": "object",
                                      "properties": {"keywordId": {"type": "integer"}},
                                      "required": ["keywordId"]},
                        }
                    },
                    "required": ["selected"],
                },
                "maxOutputTokens": 2048,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
    )


def req_openai(root, key, model, user):
    return (
        f"{root}/api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "content-type": "application/json"},
        {
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "selection",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "selected": {
                                "type": "array",
                                "items": {"type": "object", "additionalProperties": False,
                                          "properties": {"keywordId": {"type": "integer"}},
                                          "required": ["keywordId"]},
                            }
                        },
                        "required": ["selected"],
                    },
                },
            },
            "max_completion_tokens": 2048,
        },
    )


def req_anthropic(root, key, model, user):
    return (
        f"{root}/api.anthropic.com/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        {
            "model": model,
            "max_tokens": 2048,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": user}],
            "tools": [{
                "name": "select_keywords",
                "description": "고른 Keyword 를 보고한다.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "selected": {
                            "type": "array",
                            "items": {"type": "object",
                                      "properties": {"keywordId": {"type": "integer"}},
                                      "required": ["keywordId"]},
                        }
                    },
                    "required": ["selected"],
                },
            }],
            "tool_choice": {"type": "tool", "name": "select_keywords"},
        },
    )


def parse_gemini(p):
    text = p["candidates"][0]["content"]["parts"][0]["text"]
    u = p.get("usageMetadata") or {}
    return json.loads(text), u.get("promptTokenCount"), u.get("candidatesTokenCount")


def parse_openai(p):
    text = p["choices"][0]["message"]["content"]
    u = p.get("usage") or {}
    return json.loads(text), u.get("prompt_tokens"), u.get("completion_tokens")


def parse_anthropic(p):
    block = next(b for b in p["content"] if b.get("type") == "tool_use")
    u = p.get("usage") or {}
    return block["input"], u.get("input_tokens"), u.get("output_tokens")


VENDORS = [
    ("gemini-2.5-flash",       req_gemini,    parse_gemini),
    ("gemini-2.5-flash-lite",  req_gemini,    parse_gemini),
    ("gpt-4o-mini",            req_openai,    parse_openai),
    ("gpt-4.1-mini",           req_openai,    parse_openai),
    ("gpt-4.1-nano",           req_openai,    parse_openai),
    ("claude-haiku-4-5-20251001", req_anthropic, parse_anthropic),
]


async def run(client, root, key, model, build_req, parse, user, n, gap):
    lat, sels, errs = [], [], []
    pt = ot = None
    for i in range(n):
        url, headers, body = build_req(root, key, model, user)
        t0 = time.monotonic()
        try:
            r = await client.post(url, headers=headers, json=body)
            dt = time.monotonic() - t0
            if r.status_code != 200:
                errs.append(f"{r.status_code}:{r.text[:60]}")
            else:
                obj, p_tok, o_tok = parse(r.json())
                lat.append(dt)
                pt, ot = p_tok, o_tok
                sels.append(tuple(sorted(s["keywordId"] for s in obj.get("selected", []))))
        except Exception as exc:  # noqa: BLE001 — 프로브다. 무엇이 터지든 기록하고 계속
            errs.append(f"EXC:{str(exc)[:60]}")
        if i < n - 1:
            await asyncio.sleep(gap)

    uniq = set(sels)
    return {
        "model": model, "ok": len(lat), "err": len(errs), "errors": errs[:3],
        "avg": statistics.mean(lat) if lat else 0.0,
        "min": min(lat) if lat else 0.0, "max": max(lat) if lat else 0.0,
        "prompt": pt, "output": ot,
        "selections": [list(s) for s in sels],
        "stable": len(uniq) == 1 and bool(uniq),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--gap", type=float, default=1.0)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--place", default=CONTEXTS[0][0],
                    help="CONTEXTS 의 kakao_place_id 중 하나")
    args = ap.parse_args()

    s = get_settings()
    root = s.gms_base_url.split("/gmsapi/")[0] + "/gmsapi"
    sel_place, sel_ctx = next(
        ((k, t) for k, t in CONTEXTS if k == args.place), (args.place, "")
    )
    if not sel_ctx:
        raise SystemExit(f"CONTEXTS 에 없는 키: {args.place}")
    cands = await load_candidates(sel_place, args.top_k)
    user = build_user(sel_ctx, cands)

    print("=" * 88)
    print(f"벤더 비교  {sel_place}  후보 {len(cands)}개 · {args.n}회씩 · 간격 {args.gap}s")
    print(f"  Context: {sel_ctx[:60]}…")
    print("=" * 88)

    results = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for model, build_req, parse in VENDORS:
            print(f"  … {model}")
            results.append(await run(client, root, s.gms_api_key, model,
                                     build_req, parse, user, args.n, args.gap))

    print(f"\n{'모델':<28}{'성공':>5}{'실패':>5}{'평균':>8}{'최소':>7}{'최대':>7}"
          f"{'prompt':>8}{'output':>8}  결과")
    base = None
    for r in results:
        sel = r["selections"][0] if r["selections"] else []
        if r["model"] == "gemini-2.5-flash":
            base = set(sel)
        mark = "고정" if r["stable"] else "흔들림"
        print(f"{r['model']:<28}{r['ok']:>5}{r['err']:>5}{r['avg']:>7.2f}s{r['min']:>6.2f}s"
              f"{r['max']:>6.2f}s{str(r['prompt']):>8}{str(r['output']):>8}  {sel} {mark}")
        for e in r["errors"]:
            print(f"{'':<28}  ! {e}")

    if base is not None:
        print(f"\n현행(gemini-2.5-flash) 선택과의 일치도  기준 {sorted(base)}")
        for r in results:
            if not r["selections"]:
                continue
            got = set(r["selections"][0])
            inter = base & got
            j = len(inter) / len(base | got) if (base | got) else 1.0
            print(f"  {r['model']:<28} 교집합 {len(inter)}/{len(base | got)}  Jaccard {j:.2f}"
                  f"  누락 {sorted(base - got)}  추가 {sorted(got - base)}")

    os.makedirs(".demo", exist_ok=True)
    with open(f".demo/vendor-probe-{sel_place}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n원본: .demo/vendor-probe.json")
    return 0


sys.exit(asyncio.run(main()))

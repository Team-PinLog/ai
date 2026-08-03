"""축 B — **이미지 크기가 토큰을 어떻게 움직이는가.** `S15P11A705-253` 의 본문.

    .venv/Scripts/python.exe tools/gms_image/probe_vision.py --plan          # 안 부르고 계획만
    .venv/Scripts/python.exe tools/gms_image/probe_vision.py --run main
    .venv/Scripts/python.exe tools/gms_image/probe_vision.py --run ceiling --used 21

출발점은 `-227` 이 남긴 한 줄이다 — **1×1 PNG 한 장에 `prompt_tokens` 8,524.** 그 수가
설명되지 않아 `ai#98`(장소 제안)의 비용 예측이 안 선다. 68바이트짜리 이미지가 텍스트
프롬프트의 300배를 먹는다면 셋 중 하나다.

    A  게이트웨이 가산    GMS 가 이미지에 상수를 얹는다        → 치수·바이트 어느 쪽과도 무관
    B  base64 그대로     게이트웨이가 이미지를 텍스트로 센다   → **바이트**에 비례
    C  정상             벤더 자체 타일 규칙                  → **치수**에 반응, 바이트 무관

**세 가설은 「무엇에 비례하는가」로 갈린다.** 그래서 조건을 크기 순으로 늘어놓기만 하면
안 된다 — 실제 사진은 치수가 크면 바이트도 크므로 A·B·C 가 전부 같은 곡선을 낸다.
`synth.py` 의 대조쌍이 그 얽힘을 푼다.

    px512-solid   512×512    2,004 B ┐  치수 같음 · 바이트 393배 차이
    px512-noise   512×512  787,252 B ┘

    토큰이 같다        → B 기각 (바이트가 393배인데 안 움직였다)
    토큰이 393배 차이  → B 채택
    치수를 늘렸을 때만 움직인다 → C
    치수를 늘려도 안 움직인다   → A

## 왜 OpenAI 경로에 표본을 몰아 두나

8,524 가 거기서 나왔다. 원인을 규명해야 하는 것은 **그 수**이지 "이미지 일반"이 아니다.
다른 두 경로는 같은 대조쌍만 1회씩 밟아 **판정이 벤더에 국한되는지**를 본다 — 세 경로가
같은 결론이면 게이트웨이 공통 동작이고, OpenAI 만 다르면 벤더 과금 규칙이다. 그 갈림이
가설 A 와 C 의 차이다.

## `detail` 을 왜 재나

OpenAI 는 `image_url.detail` 로 이미지 처리 해상도를 고른다. `ai#98` 이 대화 캡처를
분석한다면 **이것이 유일하게 우리 손에 있는 비용 손잡이**다. 기본값(auto)과 `low` 의
차이를 재 두지 않으면 「비쌉니다」로 끝나고 대안을 제시할 수 없다.

## 거부 임계 (`-225` 와 겹친다)

`px2048-noise` 는 12.6 MB(base64 16.8 MB)다. Anthropic 문서상 이미지 상한(5 MB)을 넘고
게이트웨이 본문 상한도 넘을 수 있다. **거부가 어디서 오는가**가 측정 대상이다 —
`[GMS 에러]` 면 게이트웨이, 벤더 원문이면 벤더다. `-205` T62 는 거대 본문에서 게이트웨이가
"모델을 못 찾겠다"는 400 을 먼저 내는 것을 봤다. 그 오진이 이미지에도 나오면 `-225`
(긴 Context 가 모델 오류로 오진)와 같은 얼굴이다.
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gms_image.gateway import (  # noqa: E402
    Budget,
    BudgetExceeded,
    Recorder,
    call,
    load_creds,
    log,
    now_kst,
)
from tools.gms_image.synth import build  # noqa: E402

# 칩이 준 상한.
MAX_CALLS = 30

# 세 경로에 **같은 문장**을 준다. 텍스트 토큰이 상수여야 이미지 토큰을 뺄셈으로 분리할 수
# 있다. `-227` 이 쓴 문장 그대로라 그쪽 8,524 와 직접 비교된다.
PROMPT = "이 이미지에 무엇이 보이는가? 한 단어로 답하라"

# 출력 토큰을 최소로. 재는 것은 입력 쪽이고, 출력은 비용만 늘린다.
MAX_OUT = 16

# `-227` 이 쓴 것과 같은 모델. 다른 모델을 고르면 8,524 와 비교가 성립하지 않는다.
MODELS = {
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.5-flash-lite",
    "anthropic": "claude-haiku-4-5-20251001",
}

PUBLIC = tuple(MODELS.values())


def _body(vendor: str, data: bytes, detail: str | None) -> tuple[str, dict, dict]:
    """`(path, headers, body)`. 벤더 스펙 그대로 — `app/client/vendors.py` 와 같은 세 모양."""
    b64 = base64.b64encode(data).decode("ascii")
    model = MODELS[vendor]
    if vendor == "openai":
        image_url = {"url": f"data:image/png;base64,{b64}"}
        if detail:
            image_url["detail"] = detail
        return (
            "/api.openai.com/v1/chat/completions",
            {"Authorization": "Bearer {key}", "content-type": "application/json"},
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {"type": "image_url", "image_url": image_url},
                        ],
                    }
                ],
                "max_completion_tokens": MAX_OUT,
            },
        )
    if vendor == "gemini":
        return (
            f"/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            {"x-goog-api-key": "{key}", "content-type": "application/json"},
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": PROMPT},
                            {"inlineData": {"mimeType": "image/png", "data": b64}},
                        ],
                    }
                ],
                # thinking 을 끈다 — 켜면 출력 토큰이 늘어 입력 쪽 신호가 묻힌다
                # (`vendors.py` 의 판단과 같다).
                "generationConfig": {
                    "maxOutputTokens": MAX_OUT,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
        )
    return (
        "/api.anthropic.com/v1/messages",
        {
            "x-api-key": "{key}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        {
            "model": model,
            "max_tokens": MAX_OUT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                    ],
                }
            ],
        },
    )


def _usage(vendor: str, payload: dict | None) -> dict:
    """봉투에서 usage 만 꺼낸다. **정규화는 하지 않는다** — 그것은 `report.py` 몫이다.

    여기서 벤더별 필드를 하나로 접으면 `--replay` 가 판정을 다시 할 때 원본이 없다.
    """
    if not isinstance(payload, dict):
        return {}
    return payload.get("usage") or payload.get("usageMetadata") or {}


def _answer(vendor: str, payload: dict | None) -> str | None:
    """모델이 실제로 답한 문자열. **이미지를 읽었는지**의 유일한 증거다.

    usage 만 보면 게이트웨이가 이미지를 버리고 텍스트만 넘겼어도 알 수 없다.
    """
    if not isinstance(payload, dict):
        return None
    try:
        if vendor == "openai":
            return payload["choices"][0]["message"]["content"]
        if vendor == "gemini":
            return payload["candidates"][0]["content"]["parts"][0]["text"]
        return payload["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None


# 측정 계획. `(vendor, image_id, detail, reps)`
#
# main     가설 판정. OpenAI 에 표본을 몰고, 나머지 둘은 대조쌍만 밟는다
# ceiling  거부 임계. 12.6 MB 를 세 경로에 한 번씩 — 어디서 거부하는지가 답이다
PLANS: dict[str, tuple[tuple[str, str, str | None, int], ...]] = {
    "main": (
        # `-227` 의 8,524 재현. 여기가 안 맞으면 아래 전부가 다른 이야기다.
        ("openai", "px1-solid", None, 2),
        ("openai", "px64-noise", None, 2),
        # ── 대조쌍. 치수 같음 · 바이트 393배 ──
        ("openai", "px512-solid", None, 2),
        ("openai", "px512-noise", None, 2),
        ("openai", "px1024-noise", None, 2),
        # 비용 손잡이. 같은 이미지에 detail 만 바꾼다
        ("openai", "px512-noise", "low", 1),
        ("openai", "px1024-noise", "low", 1),
        # 다른 두 경로 — 판정이 벤더에 국한되는지
        ("gemini", "px1-solid", None, 1),
        ("gemini", "px512-solid", None, 1),
        ("gemini", "px512-noise", None, 1),
        ("gemini", "px1024-noise", None, 1),
        ("anthropic", "px1-solid", None, 1),
        ("anthropic", "px512-solid", None, 1),
        ("anthropic", "px512-noise", None, 1),
        ("anthropic", "px1024-noise", None, 1),
    ),
    "ceiling": (
        ("openai", "px2048-noise", None, 1),
        ("gemini", "px2048-noise", None, 1),
        ("anthropic", "px2048-noise", None, 1),
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", choices=tuple(PLANS), help="생략하면 계획만 낸다")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--out", default="axis-b.jsonl")
    ap.add_argument("--limit", type=int, default=MAX_CALLS)
    ap.add_argument("--used", type=int, default=0, help="이전 실행이 이미 쓴 호출 수")
    args = ap.parse_args()

    if args.plan or not args.run:
        total = 0
        for name, plan in PLANS.items():
            n = sum(reps for _, _, _, reps in plan)
            total += n
            log(f"[{name}] {n}회")
            for vendor, image_id, detail, reps in plan:
                img = build(image_id)
                log(
                    f"  {vendor:<10} {image_id:<14} {img.width:>4}x{img.height:<4} "
                    f"{img.nbytes:>10,} B  detail={detail or 'auto':<4} ×{reps}"
                )
        log(f"합계 {total}회 (상한 {MAX_CALLS})")
        return 0

    root, key = load_creds()
    budget = Budget(args.limit)
    budget.used = args.used
    rec = Recorder(args.out)
    log(f"축 B · root={root} · {now_kst()} · 남은 상한 {budget.left}회")

    # 12.6 MB 업로드가 있다. write 타임아웃을 넉넉히 준다 — 끊기면 그것이 게이트웨이 상한인지
    # 우리 타임아웃인지 못 가른다.
    timeout = httpx.Timeout(connect=15.0, read=180.0, write=300.0, pool=15.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            for vendor, image_id, detail, reps in PLANS[args.run]:
                img = build(image_id)
                path, headers, body = _body(vendor, img.data, detail)
                headers = {k: v.replace("{key}", key) for k, v in headers.items()}
                for rep in range(1, reps + 1):
                    n = budget.take()
                    result = call(
                        client,
                        method="POST",
                        url=root + path,
                        headers=headers,
                        body=body,
                        key=key,
                        allow=PUBLIC,
                    )
                    usage = _usage(vendor, result["payload"])
                    record = {
                        "axis": "B",
                        "call": n,
                        "rep": rep,
                        "ts_kst": now_kst(),
                        "vendor": vendor,
                        "model": MODELS[vendor],
                        "path": path,
                        "detail": detail,
                        "image": img.fingerprint(),
                        "b64_bytes": (img.nbytes + 2) // 3 * 4,
                        "usage": usage,
                        "answer": _answer(vendor, result["payload"]),
                        **result,
                    }
                    rec.write(record)
                    log(
                        f"  [{n:>2}/{budget.limit}] {vendor:<10} {image_id:<14} "
                        f"detail={detail or 'auto':<4} → {result['status']} "
                        f"({result['elapsed_ms']:>6} ms) usage={usage or '-'}"
                    )
                    if result["status"] == 429:
                        # 429 자체가 측정값이다. 무한 재시도로 쿼터를 태우지 않는다 —
                        # 남은 조건을 포기하고 멈춘다. 기록은 이미 파일에 있다.
                        log("  !! 429 — 백오프하고 멈춘다. 남은 조건은 다음 실행으로.")
                        return 0
    except BudgetExceeded as exc:
        log(f"  !! {exc}")
    finally:
        rec.close()
    log(f"  → {rec.path} (누적 {budget.used}회)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

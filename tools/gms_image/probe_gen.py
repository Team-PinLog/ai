"""축 A — **GMS 가 이미지 생성 API 를 뚫어 두었는가.**

`ai#97`(컬렉션 표지 생성)의 전제 넷 중 하나다. 여기서 「지원 안 함」이 나오면 그 설계
대부분이 무의미해지므로 **먼저 답이 나와야 한다.**

    .venv/Scripts/python.exe tools/gms_image/probe_gen.py --stage discover
    .venv/Scripts/python.exe tools/gms_image/probe_gen.py --stage profile --probe <id> --reps 5

## 대조군이 왜 있나

이미지 생성 경로가 전부 404 로 왔다고 하자. 그것이 **게이트웨이가 안 뚫었다**는 뜻인지,
**내가 URL 을 잘못 만들었다**는 뜻인지 응답만으로는 못 가른다. 그래서 벤더마다
`GET .../models`(모델 목록)를 같은 root · 같은 헤더로 먼저 부른다.

    모델 목록 200 · 생성 404   → 게이트웨이가 그 경로를 프록시하지 않는다  (답이 나왔다)
    모델 목록도 404            → root·헤더 구성이 틀렸다                  (내 문제다)
    모델 목록 401/403          → 키·권한 문제                            (다른 처방)

**「경로가 없다」와 「권한이 없다」와 「모델이 없다」는 다음 행동이 갈린다.** 대조군이
없으면 셋을 하나로 뭉뚱그린 채로 `ai#97` 에 답하게 된다.

목록 응답은 덤이 아니다 — **게이트웨이가 어떤 모델을 노출하는지**가 거기 있고, 그것이
생성 모델 가용 여부의 두 번째 증거가 된다.

## Anthropic

이미지 **생성** 엔드포인트가 공개 API 에 없다(Messages·Files·Models·Batches 뿐). 없는
경로를 두드려 404 를 받는 것은 게이트웨이가 아니라 벤더에 대한 사실이라 근거가 되지
못한다. 대신 모델 목록만 부른다 — 목록에 생성 모델이 없다는 것이 **게이트웨이가 노출한
범위**에 대한 직접 증거다. 호출 1회로 근거를 남기는 쪽이 문장으로 단언하는 것보다 낫다.
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
from tools.gms_image.synth import png_dims  # noqa: E402

# 칩이 준 상한. 사람이 세지 않는다.
MAX_CALLS = 20

# 생성 프롬프트. 합성 도형 하나 — 개인정보도, 인물도, 상표도 들어갈 여지가 없다.
PROMPT = "a plain solid blue square centered on a white background, flat vector, no text"

# 안전 정책 거절을 **일부러** 유발하는 프롬프트. 거절이 어떤 모양으로 오는지가 측정
# 대상이다 — `ai#97` 이 사용자 입력에서 표지를 만든다면 「정책 거절」과 「기술 오류」를
# 갈라 다르게 처리해야 하고, 응답에서 그 둘이 안 갈리면 설계가 성립하지 않는다.
#
# 상표 캐릭터를 쓴다. 실존 인물 초상이나 유해 소재가 아니라 **가장 순한 트리거**이고,
# 기대 결과가 「거절」이라 유해한 산출물이 나올 여지가 없다. 우회가 아니라 정반대다 —
# 거절이 응답에서 식별 가능한지 확인하는 것이다.
POLICY_PROMPT = "a portrait of Mickey Mouse, the Disney character, in his classic red shorts"

# 공개 값이라 마스킹 예외로 둔다. 없으면 모델명이 `<masked:25>` 로 가려져 기록에서
# 어느 모델을 불렀는지 못 읽는다.
PUBLIC = (
    "imagen-3.0-generate-002",
    "gemini-2.5-flash-image",
    "claude-haiku-4-5-20251001",
)


def _openai(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "content-type": "application/json"}


def _gemini(key: str) -> dict:
    return {"x-goog-api-key": key, "content-type": "application/json"}


def _anthropic(key: str) -> dict:
    return {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


# (id, kind, vendor, method, path, body-factory)
#   kind = "control" 은 대조군(모델 목록), "generate" 는 실제 이미지 생성 시도.
PROBES: tuple[tuple[str, str, str, str, str, object], ...] = (
    ("openai:models", "control", "openai", "GET", "/api.openai.com/v1/models", None),
    (
        "openai:gpt-image-1",
        "generate",
        "openai",
        "POST",
        "/api.openai.com/v1/images/generations",
        lambda: {"model": "gpt-image-1", "prompt": PROMPT, "n": 1, "size": "1024x1024"},
    ),
    (
        "openai:dall-e-3",
        "generate",
        "openai",
        "POST",
        "/api.openai.com/v1/images/generations",
        lambda: {
            "model": "dall-e-3",
            "prompt": PROMPT,
            "n": 1,
            "size": "1024x1024",
            "response_format": "b64_json",
        },
    ),
    (
        "gemini:models",
        "control",
        "gemini",
        "GET",
        "/generativelanguage.googleapis.com/v1beta/models",
        None,
    ),
    (
        "gemini:imagen-3",
        "generate",
        "gemini",
        "POST",
        "/generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict",
        lambda: {
            "instances": [{"prompt": PROMPT}],
            "parameters": {"sampleCount": 1, "aspectRatio": "1:1"},
        },
    ),
    (
        "gemini:flash-image",
        "generate",
        "gemini",
        "POST",
        "/generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent",
        lambda: {
            "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        },
    ),
    (
        "anthropic:models",
        "control",
        "anthropic",
        "GET",
        "/api.anthropic.com/v1/models",
        None,
    ),
    # ── discover 에서 돌지 않는다. 지원이 확인된 뒤 `--stage profile` 로 따로 부른다 ──
    #
    # `size` 가 **실제로 먹는지**. 정사각형 요청에 정사각형이 온 것만으로는 파라미터가
    # 먹은 것인지 기본값이 우연히 맞은 것인지 못 가른다. 세로 비율을 요청해 본다.
    (
        "openai:gpt-image-1-portrait",
        "shape",
        "openai",
        "POST",
        "/api.openai.com/v1/images/generations",
        lambda: {"model": "gpt-image-1", "prompt": PROMPT, "n": 1, "size": "1024x1536"},
    ),
    # Gemini 쪽에는 `size` 가 없다. 비율 지정 수단이 있는지(`imageConfig.aspectRatio`)를
    # 본다 — 없으면 `ai#97` 은 게이트웨이 밖에서 잘라야 한다.
    (
        "gemini:flash-image-ratio",
        "shape",
        "gemini",
        "POST",
        "/generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent",
        lambda: {
            "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": "3:4"},
            },
        },
    ),
    (
        "openai:gpt-image-1-policy",
        "policy",
        "openai",
        "POST",
        "/api.openai.com/v1/images/generations",
        lambda: {"model": "gpt-image-1", "prompt": POLICY_PROMPT, "n": 1, "size": "1024x1024"},
    ),
    (
        "gemini:flash-image-policy",
        "policy",
        "gemini",
        "POST",
        "/generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent",
        lambda: {
            "contents": [{"role": "user", "parts": [{"text": POLICY_PROMPT}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        },
    ),
)

# `--stage discover` 가 도는 것. 나머지는 지원이 확인된 뒤에만 의미가 있다.
_DISCOVER_KINDS = ("control", "generate")

_HEADERS = {"openai": _openai, "gemini": _gemini, "anthropic": _anthropic}


def _generated(payload: dict) -> dict:
    """생성된 이미지의 **치수와 실 바이트**를 꺼낸다. blob 이 접히기 전에 불린다.

    요청한 `size`·비율이 실제로 먹었는지는 이것 말고는 확인할 방법이 없다 — 응답이
    에코하는 `size` 필드는 OpenAI 쪽에만 있고, Gemini 는 아무것도 에코하지 않는다.
    """
    for extract in (
        lambda p: p["data"][0]["b64_json"],
        lambda p: p["candidates"][0]["content"]["parts"][0]["inlineData"]["data"],
    ):
        try:
            raw = base64.b64decode(extract(payload))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        dims = png_dims(raw)
        return {"out_bytes": len(raw), "out_dims": list(dims) if dims else None}
    return {}


def _run_one(client, rec, budget, root, key, probe, *, rep=1) -> dict:
    probe_id, kind, vendor, method, path, factory = probe
    n = budget.take()
    body = factory() if factory else None
    result = call(
        client,
        method=method,
        url=root + path,
        headers=_HEADERS[vendor](key),
        body=body,
        key=key,
        allow=PUBLIC,
        inspect=_generated,
    )
    record = {
        "axis": "A",
        "call": n,
        "rep": rep,
        "ts_kst": now_kst(),
        "probe": probe_id,
        "kind": kind,
        "vendor": vendor,
        "method": method,
        "path": path,
        **result,
    }
    rec.write(record)
    log(
        f"  [{n:>2}/{budget.limit}] {probe_id:<22} {method:<4} → {result['status']} "
        f"({result['elapsed_ms']} ms, body {result['body_len']} B)"
    )
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("discover", "profile"), default="discover")
    ap.add_argument("--probe", help="profile 단계에서 반복할 probe id")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default="axis-a.jsonl")
    ap.add_argument("--limit", type=int, default=MAX_CALLS)
    ap.add_argument("--used", type=int, default=0, help="이전 실행이 이미 쓴 호출 수")
    args = ap.parse_args()

    root, key = load_creds()
    budget = Budget(args.limit)
    budget.used = args.used
    rec = Recorder(args.out)
    log(f"축 A · root={root} · {now_kst()} · 남은 상한 {budget.left}회")

    # 큰 이미지를 받을 수 있으므로 read 타임아웃을 넉넉히. 생성은 수십 초가 정상이다.
    timeout = httpx.Timeout(connect=15.0, read=180.0, write=60.0, pool=15.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            if args.stage == "discover":
                for probe in PROBES:
                    if probe[1] in _DISCOVER_KINDS:
                        _run_one(client, rec, budget, root, key, probe)
            else:
                for probe_id in args.probe.split(","):
                    target = next((p for p in PROBES if p[0] == probe_id), None)
                    if target is None:
                        raise SystemExit(f"모르는 probe '{probe_id}'")
                    for rep in range(1, args.reps + 1):
                        _run_one(client, rec, budget, root, key, target, rep=rep)
    except BudgetExceeded as exc:
        log(f"  !! {exc}")
    finally:
        rec.close()
    log(f"  → {rec.path} (누적 {budget.used}회)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

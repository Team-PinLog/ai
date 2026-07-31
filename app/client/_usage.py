"""GMS 응답의 토큰 사용량을 선택적으로 기록한다.

`PINLOG_TOKEN_LOG` 환경변수에 파일 경로가 있을 때만 동작하고, 없으면 아무것도 하지
않는다. 운영 경로에 영향을 주지 않으면서 E2E 실측에서 비용을 셀 수 있게 하는 것이
목적이다.

왜 필요한가: 임베딩(OpenAI 호환 `usage`)과 판정 응답에 토큰 수가 실려 오는데 두
클라이언트가 모두 버리고 있었다. 2026-07-30 실데이터 시딩에서 토큰량을 요구받았을 때
앱 어디에도 그 값이 남지 않는다는 것이 드러났다.

**판정은 벤더마다 필드 이름이 다르다.** 폴백 체인(S15P11A705-175)이 들어오면서 한 배포
안에서 세 형식이 섞여 들어온다 — 그래서 행에 `vendor`·`model`을 함께 남긴다. 이 둘이
없으면 "이 호출이 몇 토큰을 썼나"는 알아도 "어느 경로로 나갔나"는 알 수 없고, 폴백이
실제로 발동했는지 사후에 확인할 방법이 사라진다.

기록 실패는 삼킨다 — 계측이 본 작업을 죽이면 안 된다.
"""

from __future__ import annotations

import json
import os
import threading
import time

_LOCK = threading.Lock()


def _embedding_tokens(payload: dict) -> dict:
    usage = payload.get("usage") or {}
    return {
        "prompt": usage.get("prompt_tokens"),
        "total": usage.get("total_tokens"),
    }


def _openai_tokens(payload: dict) -> dict:
    usage = payload.get("usage") or {}
    return {
        "prompt": usage.get("prompt_tokens"),
        "output": usage.get("completion_tokens"),
        "total": usage.get("total_tokens"),
    }


def _gemini_tokens(payload: dict) -> dict:
    usage = payload.get("usageMetadata") or {}
    return {
        "prompt": usage.get("promptTokenCount"),
        "output": usage.get("candidatesTokenCount"),
        # thinking을 끄고 부르므로 0이 정상이다. 값이 붙기 시작하면 설정이 새고 있다는 뜻.
        "thoughts": usage.get("thoughtsTokenCount"),
        "total": usage.get("totalTokenCount"),
    }


def _anthropic_tokens(payload: dict) -> dict:
    usage = payload.get("usage") or {}
    prompt, output = usage.get("input_tokens"), usage.get("output_tokens")
    return {
        "prompt": prompt,
        "output": output,
        # Anthropic은 합계를 주지 않는다. 다른 벤더 행과 같은 키로 집계하려면 여기서 더한다.
        "total": None if prompt is None or output is None else prompt + output,
    }


# 벤더 이름은 `app.client.vendors.ADAPTERS`의 키와 같아야 한다. 어긋나면 그 벤더로 나간
# 호출의 토큰이 조용히 None으로 기록된다 — tests/test_llm_vendors.py가 두 목록을 대조한다.
_JUDGE_TOKENS = {
    "openai": _openai_tokens,
    "gemini": _gemini_tokens,
    "anthropic": _anthropic_tokens,
}

_NO_TOKENS = {"prompt": None, "output": None, "total": None}


def record(
    kind: str,
    payload: dict,
    *,
    vendor: str | None = None,
    model: str | None = None,
) -> None:
    """응답 payload 에서 토큰 수를 뽑아 JSONL 한 줄로 남긴다.

    kind: "embedding" | "judge"
    vendor: 판정에서 응답한 프로바이더 경로("openai" | "gemini" | "anthropic")
    """
    path = os.environ.get("PINLOG_TOKEN_LOG")
    if not path:
        return
    try:
        if kind == "embedding":
            row = _embedding_tokens(payload)
        else:
            row = _JUDGE_TOKENS.get(vendor or "", lambda _p: dict(_NO_TOKENS))(payload)
        row["kind"] = kind
        if vendor:
            row["vendor"] = vendor
        if model:
            row["model"] = model
        row["at"] = time.time()
        with _LOCK, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 계측이 본 작업을 죽이지 않는다
        pass

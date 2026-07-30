"""GMS 응답의 토큰 사용량을 선택적으로 기록한다.

`PINLOG_TOKEN_LOG` 환경변수에 파일 경로가 있을 때만 동작하고, 없으면 아무것도
하지 않는다. 운영 경로에 영향을 주지 않으면서 E2E 실측에서 비용을 셀 수 있게
하는 것이 목적이다.

왜 필요한가: 임베딩(OpenAI 호환 `usage`)과 판정(Gemini `usageMetadata`) 응답에
토큰 수가 실려 오는데 두 클라이언트가 모두 버리고 있었다. 2026-07-30 실데이터
시딩에서 토큰량을 요구받았을 때 앱 어디에도 그 값이 남지 않는다는 것이 드러났다.

기록 실패는 삼킨다 — 계측이 본 작업을 죽이면 안 된다.
"""

from __future__ import annotations

import json
import os
import threading
import time

_LOCK = threading.Lock()


def record(kind: str, payload: dict) -> None:
    """응답 payload 에서 토큰 수를 뽑아 JSONL 한 줄로 남긴다.

    kind: "embedding" | "judge"
    """
    path = os.environ.get("PINLOG_TOKEN_LOG")
    if not path:
        return
    try:
        if kind == "embedding":
            u = payload.get("usage") or {}
            row = {
                "kind": kind,
                "prompt": u.get("prompt_tokens"),
                "total": u.get("total_tokens"),
            }
        else:
            u = payload.get("usageMetadata") or {}
            row = {
                "kind": kind,
                "prompt": u.get("promptTokenCount"),
                "output": u.get("candidatesTokenCount"),
                "thoughts": u.get("thoughtsTokenCount"),
                "total": u.get("totalTokenCount"),
            }
        row["at"] = time.time()
        with _LOCK, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 계측이 본 작업을 죽이지 않는다
        pass

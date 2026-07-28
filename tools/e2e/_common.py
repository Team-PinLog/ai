"""E2E 드라이버 공통 설정.

레포 루트를 sys.path에 넣고 앱 설정을 그대로 재사용한다. `.env`를 셸이나 자체
파서로 읽지 않는다 — BOM·CRLF가 값에 섞이는 문제를 피하기 위함이다(T16·T22).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # tools/e2e → 레포 루트

# 앱 모듈 해석 + .env 탐색 기준을 레포 루트로 고정
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.core.config import get_settings  # noqa: E402

SETTINGS = get_settings()
CONTEXTS_YAML = HERE / "e2e_contexts.yaml"
DEFAULT_BASE = "http://localhost:8000"


def headers() -> dict:
    return {"X-Internal-Secret": SETTINGS.internal_shared_secret}


def load_contexts() -> list[dict]:
    import yaml

    return yaml.safe_load(CONTEXTS_YAML.read_text(encoding="utf-8"))["contexts"]


def base_url(argv: list[str]) -> str:
    """--base http://localhost:8001 로 Docker 컨테이너 등을 겨냥할 수 있다."""
    if "--base" in argv:
        return argv[argv.index("--base") + 1].rstrip("/")
    return DEFAULT_BASE

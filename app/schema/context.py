"""Context 처리 API 요청 스키마 (context-processing.md §1)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ContextProcessRequest(BaseModel):
    contextId: int
    userId: int
    recordId: int
    text: str
    # placeMeta는 MVP에서 임베딩 입력에 결합하지 않는다(결합 시 Embedding Profile 변경 대상,
    # model-profile.md §4). 스키마로만 받아 두고 사용하지 않는다.
    placeMeta: dict[str, Any] | None = None

"""인터페이스 레벨 Fake (HTTP mock 아님).

호출 횟수 기록은 선택이 아니라 핵심 기능이다 — 시나리오 1·5·6·7·9·13·16·18의 단언이
"호출하지 않았다"/"정확히 한 번"이기 때문이다. 동시성 테스트는 on_call 훅으로 모델 호출과
저장 사이의 창을 결정론적으로 재현한다(sleep 금지).
"""
from __future__ import annotations

import hashlib
from typing import Awaitable, Callable

import numpy as np

from app.schema.llm import JudgeResult, KeywordSelection

OnCall = Callable[[], Awaitable[None]]


def deterministic_vector(text: str, dim: int = 1536) -> list[float]:
    """텍스트 → 재현 가능한 벡터. 무작위 벡터는 유사도 순서 단언을 흔든다."""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
    rng = np.random.default_rng(seed)
    return rng.standard_normal(dim).astype(np.float32).tolist()


class FakeEmbeddingClient:
    def __init__(
        self,
        dimension: int = 1536,
        on_call: OnCall | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.dimension = dimension
        self.call_count = 0
        self._on_call = on_call
        self._raise = raise_exc

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        if self._on_call is not None:
            await self._on_call()
        if self._raise is not None:
            raise self._raise
        return [deterministic_vector(t, self.dimension) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class FakeLLMClient:
    def __init__(
        self,
        selected: list[tuple[int, float | None]] | None = None,
        unmatched: list[str] | None = None,
        on_call: OnCall | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.call_count = 0
        self._selected = selected or []
        self._unmatched = unmatched or []
        self._on_call = on_call
        self._raise = raise_exc

    async def judge(self, context_text: str, candidates: list[dict]) -> JudgeResult:
        self.call_count += 1
        if self._on_call is not None:
            await self._on_call()
        if self._raise is not None:
            raise self._raise
        return JudgeResult(
            selected=[
                KeywordSelection(keyword_id=k, confidence=c) for k, c in self._selected
            ],
            unmatched_concepts=list(self._unmatched),
        )

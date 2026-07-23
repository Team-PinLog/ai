"""Embedding API 클라이언트 (GMS 게이트웨이, OpenAI 호환 /embeddings).

tools/keyword_eval/embed.py의 동기 클라이언트를 async로 포팅했다. 파일 캐시는
평가용이므로 제거하고, httpx.AsyncClient로 요청당 1회 호출한다.

client는 DB를 모른다(architecture.md §3). 차원 불일치는 영구 오류로 분류한다
(model-profile.md §5).
"""
from __future__ import annotations

import httpx

from app.core.errors import PermanentError, TransientError

_BATCH = 128
_TIMEOUT = 60.0


def preset_embed_text(preset: dict) -> str:
    """Preset 임베딩 입력 텍스트. Context 검색과 필드 구성을 맞춘다.

    tools/keyword_eval/embed.py와 동일한 구성:
        "{display_name}. {description} {examples를 공백으로 연결}"
    """
    examples = " ".join(preset.get("examples", []))
    return f"{preset['display_name']}. {preset['description']} {examples}".strip()


class EmbeddingClient:
    def __init__(self, base_url: str, api_key: str, model: str, dimension: int) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 목록을 임베딩. 순서를 보존한다."""
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for start in range(0, len(texts), _BATCH):
                batch = texts[start : start + _BATCH]
                vectors.extend(await self._embed_batch(client, batch))
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]

    async def _embed_batch(
        self, client: httpx.AsyncClient, batch: list[str]
    ) -> list[list[float]]:
        try:
            resp = await client.post(
                f"{self._base}/embeddings",
                headers={"Authorization": f"Bearer {self._key}"},
                json={"model": self._model, "input": batch},
            )
        except httpx.HTTPError as exc:
            raise TransientError(f"embedding request failed: {exc}") from exc

        if resp.status_code >= 500:
            raise TransientError(f"embedding server error: {resp.status_code}")
        if resp.status_code != 200:
            raise PermanentError(
                f"embedding client error: {resp.status_code} {resp.text[:200]}"
            )

        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        out: list[list[float]] = []
        for item in data:
            vec = item["embedding"]
            if len(vec) != self._dimension:
                raise PermanentError(
                    f"embedding dimension {len(vec)} != expected {self._dimension}"
                )
            out.append(vec)
        return out

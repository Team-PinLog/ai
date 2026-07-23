"""개인 자연어 검색.

질의는 분해하지 않고 전체를 한 번 임베딩한다(personal-search.md §2). 요청 Profile이
서버 설정 Profile과 다르면 임베딩을 호출하지 않고 422로 거부한다(model-profile.md §3.1).
유사도 하한 컷오프는 적용하지 않는다 — 최종 노출은 Spring이 판단한다.
"""
from __future__ import annotations

from app.client.embedding_client import EmbeddingClient
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ProfileMismatchError
from app.repository import context_embedding_repo


class SearchService:
    def __init__(
        self,
        db: Database,
        embedding_client: EmbeddingClient,
        settings: Settings,
    ) -> None:
        self._db = db
        self._embedding = embedding_client
        self._settings = settings

    async def search(
        self, user_id: int, query: str, limit: int, embedding_profile: str
    ) -> list[dict]:
        if embedding_profile != self._settings.embedding_profile:
            raise ProfileMismatchError(
                embedding_profile, self._settings.embedding_profile
            )

        query_embedding = await self._embedding.embed_one(query)

        async with self._db.acquire() as conn:
            rows = await context_embedding_repo.search(
                conn, user_id, embedding_profile, query_embedding, limit
            )

        return [
            {"recordId": r["record_id"], "similarity": round(float(r["similarity"]), 4)}
            for r in rows
        ]

"""개인 자연어 검색.

질의는 분해하지 않고 전체를 한 번 임베딩한다(personal-search.md §2). 요청 Profile이
서버 설정 Profile과 다르면 임베딩을 호출하지 않고 422로 거부한다(model-profile.md §3.1).

결과에는 두 컷을 함께 건다(personal-search.md §6.1, S15P11A705-213 실측). 절대 하한
`τ_abs`는 「이 사용자에게 관련 기록이 없다」를 0건으로 표현하고, 상대 컷 `r`은 1위 대비
급이 다른 꼬리를 자른다. **하나가 다른 하나를 대체하지 않는다** — `r`은 1위를 언제나
남기므로 무관 질의를 침묵시킬 수 없고, `τ_abs`는 질의마다 다른 유사도 대역을 따라가지
못한다.
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
            {
                "recordId": r["record_id"],
                "contextId": r["context_id"],
                "similarity": round(float(r["similarity"]), 4),
            }
            for r in self._cut(rows)
        ]

    def _cut(self, rows: list) -> list:
        """`τ_abs`와 `r`을 건다. Query가 아니라 여기서 거는 이유는 §6.1에 있다.

        두 컷 모두 유사도 하위만 자르므로 `LIMIT` 뒤에 걸어도 `WHERE`에 넣은 것과 결과가
        같다 — 상위 N개를 고른 뒤 그중 하위를 버리는 것과, 하위를 버린 뒤 상위 N개를
        고르는 것이 같다(유사도 단조). 그래서 §4의 Query를 건드리지 않는다.

        `rows`는 유사도 내림차순이다(Query의 바깥 `ORDER BY similarity DESC`).
        """
        if not rows:
            return rows
        floor = self._settings.search_similarity_floor
        ratio = self._settings.search_top_ratio
        if floor <= 0 and ratio <= 0:
            return rows
        # 1위는 컷 전 결과의 1위다. 컷 후 재계산하면 남은 것의 1위로 기준이 옮겨가
        # 아무것도 더 잘리지 않는 자기충족 컷이 된다.
        top = float(rows[0]["similarity"])
        return [
            r
            for r in rows
            if float(r["similarity"]) >= floor and float(r["similarity"]) >= ratio * top
        ]

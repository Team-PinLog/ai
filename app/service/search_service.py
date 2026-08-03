"""개인 자연어 검색.

질의는 분해하지 않고 전체를 한 번 임베딩한다(personal-search.md §2). 요청 Profile이
서버 설정 Profile과 다르면 임베딩을 호출하지 않고 422로 거부한다(model-profile.md §3.1).

결과에는 두 컷을 함께 건다(personal-search.md §6.1, S15P11A705-213 실측). 절대 하한
`τ_abs`는 「이 사용자에게 관련 기록이 없다」를 0건으로 표현하고, 상대 컷 `r`은 1위 대비
급이 다른 꼬리를 자른다. **하나가 다른 하나를 대체하지 않는다** — `r`은 1위를 언제나
남기므로 무관 질의를 침묵시킬 수 없고, `τ_abs`는 질의마다 다른 유사도 대역을 따라가지
못한다.

**`τ_abs` 는 질의 길이로 갈린다**(S15P11A705-266 실측). 「`τ_abs` 가 질의마다 다른 대역을
따라가지 못한다」는 위 한계가 단어형 질의에서 실제 손실로 나타났다 — 문장형에 맞춘 0.30
이 단어형에서 컷 전 **1위**인 정답 5건을 0건으로 만든다(`ai#87`). 두 대역이 겹치지 않아
(문장형 정답 하한 0.3642 · 단어형 0.2438) 한 값으로는 한쪽이 반드시 손해를 본다.
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
            for r in self._cut(rows, query)
        ]

    def _is_word_query(self, query: str) -> bool:
        """단어형인가. **공백이 없고 짧을 때만** 그렇다 — 두 조건을 함께 요구한다.

        어느 하나만 쓰면 경계 밖 질의가 낮은 하한을 타고 잡음을 통과시킨다. 둘 다 요구하면
        애매한 질의가 문장형(더 세게 자름) 쪽으로 기울어 안전하다.

        **공백은 `str.isspace()` 로 본다 — U+0020 만 보면 위 안전 방향이 뒤집힌다.**
        전각 공백(U+3000)·탭·NBSP 로 띄운 2어절 질의가 「공백 없음」으로 통과해 오히려
        **느슨한** 하한을 타기 때문이다. 요청 스키마에 정규화가 없어 원문이 그대로 도달하므로
        (IME 전각 모드·타 화면 복사) 실제로 닿는 경로다. `strip()` 도 같은 기준이라 앞뒤
        공백 처리와 내부 판정이 어긋나지 않는다.

        `-266` 이 측정한 단어형은 전부 공백 없는 2~5자라 **이 대역을 재지는 않았다** —
        구분자를 넓힌 것은 측정이 아니라 안전 방향으로의 판단이다(리포트 §말할 수 없는 것).
        경계값 「≤5자」도 같은 성격이며 `-255` 가 길이 상관에서 쓴 값을 따랐다.
        """
        q = query.strip()
        return (
            bool(q)
            and not any(c.isspace() for c in q)
            and len(q) <= self._settings.search_word_query_max_chars
        )

    def _cut(self, rows: list, query: str = "") -> list:
        """`τ_abs`와 `r`을 건다. Query가 아니라 여기서 거는 이유는 §6.1에 있다.

        두 컷 모두 유사도 하위만 자르므로 `LIMIT` 뒤에 걸어도 `WHERE`에 넣은 것과 결과가
        같다 — 상위 N개를 고른 뒤 그중 하위를 버리는 것과, 하위를 버린 뒤 상위 N개를
        고르는 것이 같다(유사도 단조). 그래서 §4의 Query를 건드리지 않는다.

        **`τ_abs` 는 질의 길이로 갈린다**(S15P11A705-266). 단어형과 문장형의 정답 유사도
        대역이 겹치지 않아 단일값으로는 한쪽이 반드시 손해를 본다 — 문장형에 맞춘 0.30 은
        단어형에서 **컷 전 1위인 정답**을 잘라내고(`비건` → 플랜트가 1위인데 0건),
        단어형에 맞춘 0.24 는 문장형 무관 질의 침묵을 11/15 에서 5/15 로 무너뜨린다.

        `r` 은 가르지 않는다. 상대 컷이라 대역 차이를 자동으로 흡수하고, 실측에서도 단어형
        손실이 `r=0.75` 까지 0 이었다.

        `rows`는 유사도 내림차순이다(Query의 바깥 `ORDER BY similarity DESC`).
        """
        if not rows:
            return rows
        ratio = self._settings.search_top_ratio
        # **비상 스위치는 분기보다 앞이다.** `-213` 이 이 가드를 넣었을 때 분기가 없었고
        # 분기는 `-266` 이 만들었다 — 나중에 생긴 것이 먼저 있던 안전장치를 무력화하면
        # 그것이 퇴행이다. 뒤에 두면 비상 스위치(`SEARCH_SIMILARITY_FLOOR=0` ·
        # `SEARCH_TOP_RATIO=0`)를 넣어도 단어형만 0.24 로 계속 잘리고, 장애 중에 그것을
        # 알아채야 한다.
        if self._settings.search_similarity_floor <= 0 and ratio <= 0:
            return rows
        floor = (
            self._settings.search_similarity_floor_word
            if self._is_word_query(query)
            else self._settings.search_similarity_floor
        )
        # 1위는 컷 전 결과의 1위다. 컷 후 재계산하면 남은 것의 1위로 기준이 옮겨가
        # 아무것도 더 잘리지 않는 자기충족 컷이 된다.
        top = float(rows[0]["similarity"])
        return [
            r
            for r in rows
            if float(r["similarity"]) >= floor and float(r["similarity"]) >= ratio * top
        ]

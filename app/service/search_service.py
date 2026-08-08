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

import numpy as np

from app.cache.preset_cache import PresetCache
from app.client.embedding_client import EmbeddingClient
from app.client.rewrite_client import RewriteClient
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import PermanentError, ProfileMismatchError, TransientError
from app.core.logging import get_logger
from app.repository import context_embedding_repo, context_keyword_repo

log = get_logger("app.service.search")


class SearchService:
    def __init__(
        self,
        db: Database,
        embedding_client: EmbeddingClient,
        settings: Settings,
        rewrite_client: RewriteClient | None = None,
        preset_cache: PresetCache | None = None,
    ) -> None:
        self._db = db
        self._embedding = embedding_client
        self._settings = settings
        self._rewrite = rewrite_client
        self._preset_cache = preset_cache

    async def search(
        self, user_id: int, query: str, limit: int, embedding_profile: str
    ) -> list[dict]:
        if embedding_profile != self._settings.embedding_profile:
            raise ProfileMismatchError(
                embedding_profile, self._settings.embedding_profile
            )

        # LLM 재작성 (S15P11A705-337, P49 §3). 기본 off. 실패·타임아웃이면 원문으로
        # 검색한다 — 오류가 아니라 강등이고, 강등 시 동작은 이 기능 도입 전과 동일하다.
        # 이후 임베딩·컷 판정은 전부 재작성된 질의 기준이다 — 컷의 단어형/문장형 분기는
        # 임베딩된 텍스트의 유사도 대역을 따라가는 장치이므로, 임베딩 입력과 판정 입력이
        # 갈리면 안 된다.
        query_text = query
        if (
            self._settings.search_llm_enabled
            and self._rewrite is not None
            and self._should_rewrite(query)
        ):
            try:
                query_text = await self._rewrite.rewrite(query)
            except (TransientError, PermanentError):
                query_text = query

        query_embedding = await self._embedding.embed_one(query_text)

        async with self._db.acquire() as conn:
            rows = await context_embedding_repo.search(
                conn, user_id, embedding_profile, query_embedding, limit
            )
            # 컷이 후보를 확정한 **뒤에** keyword 재정렬이 순서만 조정한다(P49 §4).
            # 재정렬은 같은 커넥션으로 context_keyword 를 읽어야 해서 컷을 acquire
            # 안으로 옮겼다 — 컷은 순수 계산이라 위치가 결과를 바꾸지 않는다.
            kept = self._cut(rows, query_text)
            kept, keyword_matched = await self._rerank_by_keyword(
                conn, user_id, kept, query_embedding
            )

        return [
            {
                "recordId": r["record_id"],
                "contextId": r["context_id"],
                "similarity": round(float(r["similarity"]), 4),
                # 재정렬이 이미 계산하는 match 여부를 버리지 않고 싣는다
                # (S15P11A705-399, OFFTOPIC-CONFIDENCE-GATE-HANDOFF-DRAFT.md §4.2 S3).
                # 재정렬이 계산되지 않은 모든 경로(off·오류·후보 없음)에서
                # `keyword_matched` 는 빈 집합이라 여기서 자연히 False 다.
                "keywordMatched": r["record_id"] in keyword_matched,
            }
            for r in kept
        ]

    def _should_rewrite(self, query: str) -> bool:
        """재작성 게이트 — 앞뒤 공백을 정리한 질의가 짧을 때만 재작성한다(I55).

        실측에서 재작성의 이득(약어 회복: `부캠` 컷 전 8위→1위·`신한 부캠` 4위→3위)은
        전부 5자 이하 질의에서 났고, 손해는 긴 문장형 질의에서만 났다 — 의미가 같은
        표현 정규화(`파는 데`→`파는 곳`)가 유사도를 컷 위로 올려 관련 없는 질의의
        무노출을 11/15 에서 9/15 로 무너뜨렸다.

        결과 부족·top-1 유사도 같은 성과 기반 게이트는 쓰지 않는다 — 회복 대상 질의도
        원문 검색이 오답을 반환하고 있어(0건이 아니다) 그 신호로는 두 집단이 갈리지
        않는 것이 실측됐다. 단어형 판정(`_is_word_query`)을 재사용하지도 않는다 —
        공백 불허 조건이 `신한 부캠`(공백 포함 5자)을 배제해 회복 1건을 잃는다.

        길이는 `len()`(코드 포인트)으로 센다. 빈 질의는 재작성할 것이 없다.
        """
        q = query.strip()
        return bool(q) and len(q) <= self._settings.search_rewrite_max_chars

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

    def _preset_candidates(self, query_embedding: list[float]) -> set[int]:
        """질의-Preset 코사인이 floor 이상인 상위 top_k Preset id (P48 1단계 규칙).

        추가 모델 호출이 없다 — 검색용으로 이미 만든 질의 임베딩을 재사용하고,
        Preset 임베딩은 기동 시 적재된 메모리 캐시에서 읽는다. `BLOCKED` Preset 은
        캐시 적재 시점에 이미 빠져 있고(`preset_cache.load`), `PUBLIC`·`PRIVATE_ONLY`
        는 신호로 쓴다(P48 §1-c).

        floor 를 top_k 보다 먼저 걸고 동점은 preset_id 오름차순으로 깬다 — 측정
        하네스(`tools/search_cut/fusion.preset_candidates`)와 같은 규칙이다. 잰
        규칙과 돌리는 규칙이 다르면 채택값의 근거가 사라진다.
        """
        vector = np.asarray(query_embedding, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return set()
        query = vector / norm

        floor = self._settings.search_keyword_rerank_floor
        scored = []
        for preset in self._preset_cache.snapshot().presets:
            preset_norm = float(np.linalg.norm(preset.embedding))
            if preset_norm == 0.0:
                continue
            cos = float(preset.embedding @ query) / preset_norm
            if cos >= floor:
                scored.append((-cos, preset.id))
        scored.sort()
        return {pid for _, pid in scored[: self._settings.search_keyword_rerank_top_k]}

    async def _rerank_by_keyword(
        self, conn, user_id: int, kept: list, query_embedding: list[float]
    ) -> tuple[list, set[int]]:
        """컷 통과 후보의 **순서만** keyword 신호로 조정한다 (S15P11A705-339, P49 §4).

        후보를 추가·제거하지 않는다 — 관련 없는 질의에서 컷 통과가 0건이면 재정렬
        대상도 0건이므로, 이 신호만으로 관련 없는 결과가 새로 노출될 수 없다(P49 §5).
        재정렬 뒤 두 번째 절단도 없다. 이 성질은 on/off 후보 집합 불변 계약 테스트가
        고정한다(`test_search_rerank.py`).

        정렬 점수 = 원래 코사인 + weight × 신호(후보 Preset 과 match 면 1, 아니면 0).
        binary 방식·floor 0.35·weight 0.05 는 오프라인 실측이 정했다(-339 리포트).
        점수는 정렬에만 쓰고 응답의 `similarity` 는 원래 코사인 그대로다.

        어떤 단계가 실패해도 응답은 실패하지 않는다 — 그 단계만 생략하고 벡터
        순서를 그대로 반환한다(P49 §5 의 실패 시 복귀 규칙).

        두 번째 반환값은 실제로 match 한 Record id 집합이다(S15P11A705-399) — 순서를
        정하는 데만 쓰고 버리던 신호를 호출부가 응답에 실을 수 있게 넘긴다. 재정렬이
        생략된 모든 경로(off·오류·후보 없음·match 없음)에서는 빈 집합을 돌려준다.
        """
        if (
            not self._settings.search_keyword_rerank_enabled
            or self._preset_cache is None  # 조립 실수의 방어선 — rewrite 와 같은 규칙
            or len(kept) < 2  # 0·1건은 바꿀 순서가 없다
        ):
            return kept, set()
        try:
            candidates = self._preset_candidates(query_embedding)
            if not candidates:
                return kept, set()
            signal_rows = await context_keyword_repo.keywords_for_records(
                conn, user_id, [r["record_id"] for r in kept]
            )
            matched = {
                row["record_id"]
                for row in signal_rows
                if row["keyword_id"] in candidates
            }
            if not matched:
                return kept, set()
            weight = self._settings.search_keyword_rerank_weight
            # sorted 는 안정 정렬이다 — 점수가 같은 행(신호 없는 행끼리 등)은
            # 벡터 순서가 그대로 유지된다.
            reranked = sorted(
                kept,
                key=lambda r: -(
                    float(r["similarity"])
                    + (weight if r["record_id"] in matched else 0.0)
                ),
            )
            return reranked, matched
        except Exception:
            log.warning(
                "keyword rerank failed; falling back to vector order", exc_info=True
            )
            return kept, set()

"""처리 파이프라인 오케스트레이션 (context-processing.md §3).

사전 검사 → (부분 재개 판정) → Embedding 단계 → Keyword 단계. 각 단계의 시작·중단은
조건부 UPDATE의 rowcount로 결정되며, 여기서는 순서와 트랜잭션 경계만 지정한다.
"""
from __future__ import annotations

from app.core.db import Database
from app.core.logging import get_logger
from app.repository import ai_state_repo
from app.schema.context import ContextProcessRequest
from app.service.embedding_service import EmbeddingService
from app.service.keyword_service import KeywordService

log = get_logger("app.service.context_processing")

_ACTIONABLE = ("PENDING", "PROCESSING")


class ContextProcessingService:
    def __init__(
        self,
        db: Database,
        embedding_service: EmbeddingService,
        keyword_service: KeywordService,
    ) -> None:
        self._db = db
        self._embedding = embedding_service
        self._keyword = keyword_service

    async def process(self, req: ContextProcessRequest) -> None:
        # 4.1 사전 검사 (잠금 없음, 비용 차단용)
        async with self._db.acquire() as conn:
            pre = await ai_state_repo.precheck(conn, req.contextId)
        if pre is None:
            log.info("ctx=%s no state row, skip", req.contextId)
            return
        emb, kw = pre["embedding_status"], pre["keyword_status"]
        if emb not in _ACTIONABLE and kw not in _ACTIONABLE:
            # 둘 다 진행 불가(CANCELLED/COMPLETED/FAILED 조합) → 할 일 없음
            log.info("ctx=%s nothing actionable (emb=%s kw=%s)", req.contextId, emb, kw)
            return

        # 부분 재개 판정 조회(Embedding 재사용 여부 + 벡터)
        async with self._db.acquire() as conn:
            resume = await ai_state_repo.load_resume(conn, req.contextId)
        if resume is None:
            return

        # Embedding 단계: 재사용/생성 → 벡터(또는 None)
        vector = await self._embedding.ensure(req, resume)

        # Keyword 단계: Embedding 단계가 중단되었어도 독립 판단한다.
        # 벡터를 보유하지 못했으면 keyword_service가 fallback 조회/영구오류를 처리한다.
        await self._keyword.run(req, carried_vector=vector)

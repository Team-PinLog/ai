"""Embedding 생성·재사용·저장.

세션 경계(architecture.md §5): 시작 전이(TX)와 저장(TX3)은 짧고, 외부 API 호출은
트랜잭션 밖에서 한다. 저장 직전 FOR UPDATE로 상태를 재검사한다.
"""
from __future__ import annotations

from app.client.embedding_client import EmbeddingClient
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import PermanentError, PersistDiscarded, TransientError
from app.core.logging import get_logger
from app.repository import ai_state_repo, context_embedding_repo
from app.repository.ai_state_repo import Stage
from app.schema.context import ContextProcessRequest

log = get_logger("app.service.embedding")


def _to_list(embedding) -> list[float]:
    if hasattr(embedding, "to_list"):
        return embedding.to_list()
    return list(embedding)


class EmbeddingService:
    def __init__(
        self, db: Database, client: EmbeddingClient, settings: Settings
    ) -> None:
        self._db = db
        self._client = client
        self._settings = settings

    async def ensure(self, req: ContextProcessRequest, resume) -> list[float] | None:
        """Context 벡터를 확보한다. 재사용/생성 성공 시 벡터, 그 외 None.

        None은 (a) 시작 전이 rowcount 0(경합/CANCELLED/타 워커 완료),
        (b) COMPLETED이나 Profile 불일치(재생성 불가), (c) 일시/영구 오류를 포함한다.
        Keyword 단계는 None이어도 embedding COMPLETED면 fallback 조회로 벡터를 얻는다.
        """
        profile = self._settings.embedding_profile

        if resume["embedding_status"] == "COMPLETED":
            if resume["emb_profile"] == profile and resume["embedding"] is not None:
                return _to_list(resume["embedding"])  # 재사용(partial-resume §2)
            # COMPLETED이나 Profile 불일치 → 스스로 재생성 불가(model-profile §3.2)
            log.warning("ctx=%s embedding COMPLETED but profile mismatch", req.contextId)
            return None

        async with self._db.acquire() as conn:
            affected = await ai_state_repo.try_start(
                conn, req.contextId, Stage.EMBEDDING, self._settings.processing_expiry_sec
            )
        if affected == 0:
            return None  # 시작하지 않음(정상 종료)

        try:
            vector = await self._client.embed_one(req.text)
        except PermanentError as exc:
            log.warning("ctx=%s embedding permanent error: %s", req.contextId, exc)
            async with self._db.transaction() as conn:
                await ai_state_repo.fail(conn, req.contextId, Stage.EMBEDDING)
            return None
        except TransientError as exc:
            # 상태를 건드리지 않고 PROCESSING으로 둔다 → 만료 후 재스캔 회수
            log.info("ctx=%s embedding transient error: %s", req.contextId, exc)
            return None

        try:
            async with self._db.transaction() as conn:
                row = await ai_state_repo.lock_state(conn, req.contextId)
                if row is None or row["embedding_status"] != "PROCESSING":
                    raise PersistDiscarded()
                await context_embedding_repo.upsert(
                    conn, req.contextId, req.userId, req.recordId, vector, profile
                )
                if await ai_state_repo.complete(conn, req.contextId, Stage.EMBEDDING) == 0:
                    raise PersistDiscarded()
        except PersistDiscarded:
            log.info("ctx=%s embedding result discarded (state changed)", req.contextId)
            return None

        return vector

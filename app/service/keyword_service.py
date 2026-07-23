"""Keyword 후보 검색 + LLM 판정 + 저장.

후보 TOP-K는 Preset 캐시 벡터로 메모리에서 계산한다(keyword-preset.md §3). 후보 0개면
LLM을 호출하지 않고 "선택 0개"로 정상 완료한다. 저장은 delete-insert이며 저장 직전
FOR UPDATE로 embedding COMPLETED + keyword PROCESSING을 재검사한다.
"""
from __future__ import annotations

import numpy as np

from app.cache.preset_cache import PresetCache, PresetSnapshot
from app.client.llm_client import LLMClient
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import PersistDiscarded, TransientError
from app.core.logging import get_logger
from app.repository import ai_state_repo, context_embedding_repo, context_keyword_repo
from app.repository.ai_state_repo import Stage
from app.schema.context import ContextProcessRequest
from app.schema.llm import JudgeResult

log = get_logger("app.service.keyword")


def _to_array(embedding) -> np.ndarray:
    if hasattr(embedding, "to_numpy"):
        return embedding.to_numpy().astype(np.float32)
    return np.asarray(embedding, dtype=np.float32)


def _topk(vector: np.ndarray, snapshot: PresetSnapshot, k: int, floor: float):
    presets = snapshot.presets
    norm = float(np.linalg.norm(vector))
    if norm == 0.0 or not presets:
        return []
    query = vector / norm
    mat = np.stack([p.embedding for p in presets])
    mat_norms = np.linalg.norm(mat, axis=1)
    mat_norms[mat_norms == 0] = 1.0
    sims = (mat @ query) / mat_norms
    order = np.argsort(-sims)[:k]
    return [presets[i] for i in order if sims[i] >= floor]


class KeywordService:
    def __init__(
        self,
        db: Database,
        llm_client: LLMClient,
        preset_cache: PresetCache,
        settings: Settings,
    ) -> None:
        self._db = db
        self._llm = llm_client
        self._cache = preset_cache
        self._settings = settings

    async def run(
        self, req: ContextProcessRequest, carried_vector: list[float] | None
    ) -> None:
        async with self._db.acquire() as conn:
            affected = await ai_state_repo.try_start(
                conn, req.contextId, Stage.KEYWORD, self._settings.processing_expiry_sec
            )
        if affected == 0:
            return  # 시작하지 않음(embedding 미완료·경합·CANCELLED 등)

        vector = await self._resolve_vector(req, carried_vector)
        if vector is None:
            return  # 판정 불가 → 영구 오류 처리됨(내부에서 fail)

        snapshot = self._cache.snapshot()
        candidates = _topk(
            _to_array(vector),
            snapshot,
            self._settings.keyword_candidate_top_k,
            self._settings.similarity_floor,
        )

        if not candidates:
            # 후보 0개 → LLM 미호출, 선택 0개로 정상 완료
            await self._persist(req, [], [], snapshot.version)
            return

        cand_ids = {p.id for p in candidates}
        cand_dicts = [
            {
                "id": p.id,
                "display_name": p.display_name,
                "category": p.category,
                "description": p.description,
                "examples": p.examples,
            }
            for p in candidates
        ]
        try:
            result = await self._llm.judge(req.text, cand_dicts)
        except TransientError as exc:
            # 상태를 PROCESSING으로 둔다 → 재스캔 회수
            log.info("ctx=%s judge transient error: %s", req.contextId, exc)
            return

        selections = self._map(result, cand_ids, req.contextId)
        await self._persist(
            req, selections, result.unmatched_concepts, snapshot.version
        )

    async def _resolve_vector(
        self, req: ContextProcessRequest, carried: list[float] | None
    ) -> list[float] | None:
        if carried is not None:
            return carried
        # 경합 경로: 다른 워커가 embedding을 완료해 벡터를 보유하지 못한 경우 fallback 조회.
        async with self._db.acquire() as conn:
            row = await context_embedding_repo.load_vector(conn, req.contextId)
        if row is None or row["embedding"] is None:
            log.warning("ctx=%s keyword started but no embedding row", req.contextId)
            await self._fail(req.contextId)
            return None
        if row["embedding_profile"] != self._settings.embedding_profile:
            # Context Embedding Profile ≠ 서버 Profile → 판정 불가(영구 오류, §3.3)
            log.warning("ctx=%s keyword profile mismatch", req.contextId)
            await self._fail(req.contextId)
            return None
        return _to_array(row["embedding"]).tolist()

    def _map(
        self, result: JudgeResult, cand_ids: set[int], context_id: int
    ) -> list[tuple[int, float | None]]:
        """후보 밖 폐기 + confidence 범위 밖 폐기 + 중복은 최댓값으로 접기."""
        best: dict[int, float | None] = {}
        dropped = 0
        for s in result.selected:
            if s.keyword_id not in cand_ids:
                dropped += 1
                continue
            if s.confidence is not None and not (0.0 <= s.confidence <= 1.0):
                dropped += 1
                continue
            prev = best.get(s.keyword_id)
            if s.keyword_id not in best or (
                s.confidence is not None
                and (prev is None or s.confidence > prev)
            ):
                best[s.keyword_id] = s.confidence
        if dropped:
            log.info("ctx=%s dropped %d out-of-candidate/invalid selections", context_id, dropped)
        return list(best.items())

    async def _persist(
        self,
        req: ContextProcessRequest,
        selections: list[tuple[int, float | None]],
        unmatched: list[str],
        preset_version: int,
    ) -> None:
        try:
            async with self._db.transaction() as conn:
                row = await ai_state_repo.lock_state(conn, req.contextId)
                if (
                    row is None
                    or row["embedding_status"] != "COMPLETED"
                    or row["keyword_status"] != "PROCESSING"
                ):
                    raise PersistDiscarded()
                await context_keyword_repo.replace(
                    conn, req.contextId, selections, preset_version
                )
                await context_keyword_repo.upsert_analysis(
                    conn,
                    req.contextId,
                    preset_version,
                    unmatched,
                    self._settings.judge_model,
                )
                if await ai_state_repo.complete(conn, req.contextId, Stage.KEYWORD) == 0:
                    raise PersistDiscarded()
        except PersistDiscarded:
            log.info("ctx=%s keyword result discarded (state changed)", req.contextId)

    async def _fail(self, context_id: int) -> None:
        async with self._db.transaction() as conn:
            await ai_state_repo.fail(conn, context_id, Stage.KEYWORD)

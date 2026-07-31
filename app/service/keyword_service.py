"""Keyword 후보 검색 + LLM 판정 + 저장.

후보 TOP-K는 Preset 캐시 벡터로 메모리에서 계산한다(keyword-preset.md §3). 후보 0개면
LLM을 호출하지 않고 "선택 0개"로 정상 완료한다. 저장은 delete-insert이며 저장 직전
FOR UPDATE로 embedding COMPLETED + keyword PROCESSING을 재검사한다.
"""
from __future__ import annotations

import asyncio

import numpy as np

from app.cache.preset_cache import PresetCache, PresetSnapshot
from app.client.llm_client import LLMClient
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import PermanentError, PersistDiscarded, TransientError
from app.core.logging import get_logger
from app.repository import ai_state_repo, context_embedding_repo, context_keyword_repo
from app.repository.ai_state_repo import Stage
from app.schema.context import ContextProcessRequest
from app.schema.llm import JudgeResult
from app.service import judge_vote
from app.service._stage_log import reclaimed as _log_reclaimed

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
            start = await ai_state_repo.try_start(
                conn, req.contextId, Stage.KEYWORD, self._settings.processing_expiry_sec
            )
        if not start.started:
            return  # 시작하지 않음(embedding 미완료·경합·CANCELLED 등)
        if start.reclaimed:
            _log_reclaimed(req.contextId, "keyword", self._settings.processing_expiry_sec)

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
            # 후보 0개 → LLM 미호출, 선택 0개로 정상 완료. 부른 모델이 없으므로
            # model_profile에는 설정 1순위를 남긴다(이 행의 의미는 "판정을 하지 않았다").
            await self._persist(req, [], [], snapshot.version, self._settings.judge_model)
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
            result = await self._judge_n(req.text, cand_dicts, req.contextId)
        except PermanentError as exc:
            # §2.2: 이 단계만 PROCESSING → FAILED. embedding이 COMPLETED면 그대로 둔다.
            # 이 핸들러가 없으면 400·401·403이 BackgroundTasks까지 새어 트레이스백만
            # 남기고 단계는 PROCESSING에 머문다 → 만료 후 재스캔이 같은 호출을 반복한다.
            log.error("ctx=%s stage=keyword permanent error: %s", req.contextId, exc)
            await self._fail(req.contextId)
            return
        except TransientError as exc:
            # §2.1: 상태를 PROCESSING으로 둔다 → 만료 후 재스캔 회수
            log.warning("ctx=%s stage=keyword transient error: %s", req.contextId, exc)
            return

        selections = self._map(result, cand_ids, req.contextId)
        await self._persist(
            req,
            selections,
            result.unmatched_concepts,
            snapshot.version,
            # 폴백으로 2·3순위가 답했으면 설정의 1순위가 아니라 **답한 모델**을 남긴다.
            # `model_profile`의 용도가 "어떤 모델의 판단이었는지 구분"이므로
            # (keyword-preset.md §5.2), 설정값을 그대로 쓰면 그 구분이 거짓이 된다.
            result.model or self._settings.judge_model,
        )

    async def _judge_n(
        self, text: str, cand_dicts: list[dict], context_id: int
    ) -> JudgeResult:
        """같은 입력을 `judge_vote_n` 회 판정해 다수결로 접는다(S15P11A705-223).

        **n=1 이면 이 메서드는 현행과 구분되지 않는다** — 호출 1회, 예외는 그대로 위로
        올라가고(`return_exceptions` 로 받은 객체를 그대로 다시 던진다), 한 표면
        `votes*2 > 1` 을 넘으므로 `combine` 이 입력을 그대로 돌려준다. 되돌리기가 설정
        한 줄이라는 요구가 이 성질에 걸려 있어 테스트로 고정한다.

        **동시에 부른다.** 순차로 돌리면 지연이 n배가 되어 `PROCESSING` 만료(600s) 상한
        (`llm_client` 머리말 §3.2)을 잡아먹는다. 대신 Context 1건이 만드는 동시 호출이
        n배가 되므로, 그쪽이 이 방식의 비용이다(구현 리포트 §5).

        정족수를 못 넘기면 **아무것도 저장하지 않고 실패로 되돌린다.** 분모가 n 으로
        고정돼 있어 정족수 미달로 다수결을 돌리면 선택 0건이 나오는데, 그것은 판정
        결과가 아니라 판정 실패이고 저장하면 둘이 구분되지 않는다(`judge_vote` 머리말).
        """
        n = self._settings.judge_vote_n
        outcomes = await asyncio.gather(
            *(self._llm.judge(text, cand_dicts) for _ in range(n)),
            return_exceptions=True,
        )
        results = [r for r in outcomes if isinstance(r, JudgeResult)]
        errors = [e for e in outcomes if isinstance(e, BaseException)]

        if errors and results:
            # 정족수를 넘겨 진행하는 경우에도 남긴다 — 조용히 넘어가면 "n 을 켰는데 실제로는
            # n-1 표로 판정하고 있다"가 관측되지 않는다.
            log.warning(
                "ctx=%s stage=keyword vote n=%d succeeded=%d failed=%d: %s",
                context_id, n, len(results), len(errors),
                "; ".join(f"{type(e).__name__}: {e}" for e in errors[:3]),
            )
        if not judge_vote.has_quorum(len(results), n):
            # 던질 것을 고른다. transient 가 하나라도 있으면 그쪽이다 — 재스캔이 다시
            # 부르면 성공할 수 있는 반면, permanent 로 올리면 이 Context 는 FAILED 로
            # 굳는다. 둘 중 하나가 틀렸을 때 되돌릴 수 있는 쪽을 고른다.
            #
            # `errors` 가 비어 있을 수는 없다 — 성공 + 실패 = n 인데 실패가 0 이면
            # 성공이 n 이고 `has_quorum(n, n)` 은 n>=1 에서 항상 참이다. 그래서 빈
            # 경우를 위한 분기를 두지 않는다(두면 영원히 실행되지 않는 줄이 남는다).
            for e in errors:
                if isinstance(e, TransientError):
                    raise e
            raise errors[0]

        return judge_vote.combine(results, n)

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
        model_profile: str,
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
                    model_profile,
                )
                if await ai_state_repo.complete(conn, req.contextId, Stage.KEYWORD) == 0:
                    raise PersistDiscarded()
        except PersistDiscarded:
            log.info("ctx=%s keyword result discarded (state changed)", req.contextId)

    async def _fail(self, context_id: int) -> None:
        async with self._db.transaction() as conn:
            await ai_state_repo.fail(conn, context_id, Stage.KEYWORD)

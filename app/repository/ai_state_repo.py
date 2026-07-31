"""ai.context_ai_state 조건부 전이.

repository는 rowcount만 반환하고 중단 여부는 service가 판단한다(state-machine.md §3.2).
컬럼명 조립 경로가 있으므로 Stage 열거형 값만 SQL로 들어간다 — 임의 문자열 차단.

FastAPI가 수행 가능한 전이(state-machine.md §2):
    PENDING → PROCESSING, PROCESSING → PROCESSING(만료 재선점),
    PROCESSING → COMPLETED, PROCESSING → FAILED
PENDING·CANCELLED·retry_count·is_deleted는 절대 쓰지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

import asyncpg


class Stage(StrEnum):
    EMBEDDING = "embedding_status"
    KEYWORD = "keyword_status"


@dataclass(frozen=True)
class StartOutcome:
    """`try_start` 결과 — 시작했는가, 그리고 **무엇을 시작했는가**.

    rowcount 하나로는 신규 시작과 재선점을 가를 수 없다. 두 경우가 같은 UPDATE 를 타고
    같은 `1` 을 돌려주기 때문이다. 그런데 둘의 의미는 정반대다 — 재선점은 **앞선 처리가
    만료(600s) 안에 끝나지 못했다**는 뜻이고, 그것이 늘고 있다면 GMS 가 느려졌거나
    프로세스가 죽고 있다는 직접 증거다(S15P11A705-197).
    """

    affected: int
    prev_status: str | None
    """UPDATE 직전의 단계 상태. State 행 자체가 없으면 `None`."""

    @property
    def started(self) -> bool:
        return self.affected > 0

    @property
    def reclaimed(self) -> bool:
        """만료된 PROCESSING 을 되찾아 시작했는가.

        시작하지 못했으면(`affected == 0`) 재선점도 아니다 — 그 경우 `prev_status` 가
        `PROCESSING` 이어도 그것은 "다른 워커가 아직 살아서 붙들고 있다"는 뜻이다.
        """
        return self.started and self.prev_status == "PROCESSING"


def _rowcount(status: str) -> int:
    # asyncpg execute()는 'UPDATE N' 형태의 명령 태그를 반환한다.
    return int(status.split()[-1])


async def precheck(conn: asyncpg.Connection, context_id: int):
    """사전 검사(잠금 없음). 불필요한 API 비용 차단용이며 정합성 보장이 아니다."""
    return await conn.fetchrow(
        "SELECT embedding_status, keyword_status "
        "FROM ai.context_ai_state WHERE context_id = $1",
        context_id,
    )


async def load_resume(conn: asyncpg.Connection, context_id: int):
    """부분 재개 판정용 조인 조회(partial-resume.md §2)."""
    return await conn.fetchrow(
        """
        SELECT e.embedding,
               e.embedding_profile AS emb_profile,
               s.embedding_status,
               s.keyword_status
        FROM ai.context_ai_state s
        LEFT JOIN ai.context_embedding e ON e.context_id = s.context_id
        WHERE s.context_id = $1
        """,
        context_id,
    )


async def lock_state(conn: asyncpg.Connection, context_id: int):
    """저장 트랜잭션 안에서 상태를 잠그고 재검사한다(SELECT ... FOR UPDATE)."""
    return await conn.fetchrow(
        "SELECT embedding_status, keyword_status "
        "FROM ai.context_ai_state WHERE context_id = $1 FOR UPDATE",
        context_id,
    )


async def try_start(
    conn: asyncpg.Connection,
    context_id: int,
    stage: Stage,
    processing_expiry_sec: int,
) -> StartOutcome:
    """PENDING이거나 만료된 stale PROCESSING일 때만 PROCESSING으로 전이.

    **전이 조건은 바뀌지 않았다.** UPDATE 직전 상태를 같은 문장에서 함께 읽어 돌려주는
    것만 더했다 — 신규 시작과 재선점을 가르기 위해서다(`StartOutcome`).

    왜 쿼리를 갈랐나(S15P11A705-197 논의 2). 호출부가 미리 읽어 둔 상태로 가르려 하면
    두 곳에서 틀린다. Keyword 단계는 `try_start` 전에 상태를 읽지 않고 곧장 부르므로
    가를 근거가 아예 없고(`keyword_service.run`), Embedding 단계는 `load_resume` 과
    UPDATE 사이에 경합 창이 있어 "재선점"이 거짓 양성으로 섞인다. 재선점은 드물게
    발생하고 발생하면 조사해야 하는 신호라, 거짓 양성이 섞이면 신호 자체를 못 믿는다.

    두 CTE 는 같은 스냅샷을 보므로 `prev` 는 UPDATE **이전** 값이다. 다른 트랜잭션이
    그 사이 커밋하면 UPDATE 쪽만 새 버전을 보게 되어 둘이 갈릴 수 있으나, 그 조합은
    `affected > 0` 과 함께 성립하지 않는다 — 남이 PENDING 을 선점했다면 만료 조건에
    걸려 우리 UPDATE 가 0행이 되고, 반대 방향(PROCESSING → PENDING)은 어느 주체에게도
    허용되지 않는다(state-machine.md §2). 즉 `reclaimed` 가 참일 때 그 값은 정확하다.
    """
    col = stage.value
    keyword_guard = (
        "AND embedding_status = 'COMPLETED'" if stage is Stage.KEYWORD else ""
    )
    sql = f"""
        WITH prev AS (
            SELECT {col} AS status
            FROM ai.context_ai_state
            WHERE context_id = $1
        ),
        started AS (
            UPDATE ai.context_ai_state
            SET {col} = 'PROCESSING',
                updated_at = now()
            WHERE context_id = $1
              AND {col} IN ('PENDING', 'PROCESSING')
              AND ({col} = 'PENDING' OR updated_at < now() - $2::interval)
              {keyword_guard}
            RETURNING 1
        )
        SELECT (SELECT count(*) FROM started) AS affected,
               (SELECT status FROM prev) AS prev_status
    """
    row = await conn.fetchrow(
        sql, context_id, timedelta(seconds=processing_expiry_sec)
    )
    return StartOutcome(affected=row["affected"], prev_status=row["prev_status"])


async def complete(conn: asyncpg.Connection, context_id: int, stage: Stage) -> int:
    """PROCESSING → COMPLETED. 저장 트랜잭션 안에서 호출. WHERE 가드 유지."""
    col = stage.value
    keyword_guard = (
        "AND embedding_status = 'COMPLETED'" if stage is Stage.KEYWORD else ""
    )
    sql = f"""
        UPDATE ai.context_ai_state
        SET {col} = 'COMPLETED',
            updated_at = now()
        WHERE context_id = $1
          AND {col} = 'PROCESSING'
          {keyword_guard}
    """
    return _rowcount(await conn.execute(sql, context_id))


async def fail(conn: asyncpg.Connection, context_id: int, stage: Stage) -> int:
    """PROCESSING → FAILED (영구 오류). PROCESSING 가드로 CANCELLED를 덮지 않는다."""
    col = stage.value
    sql = f"""
        UPDATE ai.context_ai_state
        SET {col} = 'FAILED',
            updated_at = now()
        WHERE context_id = $1
          AND {col} = 'PROCESSING'
    """
    return _rowcount(await conn.execute(sql, context_id))

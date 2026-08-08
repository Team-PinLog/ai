"""ai.context_keyword (delete-insert)와 ai.context_keyword_analysis (UPSERT).

Keyword 저장은 UPSERT가 아니라 delete-insert다. 재판정 결과가 이전보다 적을 수 있어
사라져야 할 이전 Keyword를 남기면 안 된다(keyword-preset.md §5). 삭제 범위는 언제나
지금 판정 중인 그 context_id 하나다.
"""
from __future__ import annotations

import json

import asyncpg

_DELETE = "DELETE FROM ai.context_keyword WHERE context_id = $1"

# 검색 재정렬용 keyword 신호 조회 (S15P11A705-339, P49 §4).
#
# 대상은 컷을 통과한 후보 Record 뿐이다 — 이 조회는 후보를 만들지 않고 이미 확정된
# 후보의 신호만 가져온다. 조건의 의미가 P48 §1-b·§1-c 그대로다.
#
#   keyword_status = 'COMPLETED'   신호의 조건이지 후보의 조건이 아니다. 미완료
#                                  Context 의 Record 도 벡터 후보로는 그대로 남고,
#                                  이 조회에서 행이 안 나올 뿐이다(§1-b)
#   visibility(BLOCKED 제외)       여기서 거르지 않는다 — 질의-Preset 후보를 만드는
#                                  PresetCache 가 적재 시점에 BLOCKED 를 제외하므로
#                                  (preset_cache.load), BLOCKED keyword 는 후보 집합과
#                                  매치될 수 없다. PUBLIC·PRIVATE_ONLY 는 쓴다(§1-c)
#
# Record 소속 Context 는 ai.context_embedding 으로 잇는다 — core.context 는 공용
# 계약이 접근을 금지하고, keyword 판정은 embedding 완료 뒤에만 도니 keyword 를 가진
# Context 는 embedding 행을 반드시 갖는다. DISTINCT 는 한 Record 의 여러 Context 가
# 같은 keyword 를 가질 때의 중복 행 제거다(신호 집계가 Record 단위 max 라 중복은
# 결과를 바꾸지 않지만 행 수만 늘린다).
_KEYWORDS_FOR_RECORDS = """
SELECT DISTINCT e.record_id, ck.keyword_id
FROM ai.context_embedding e
JOIN ai.context_ai_state s ON s.context_id = e.context_id
JOIN ai.context_keyword ck ON ck.context_id = e.context_id
WHERE e.user_id = $1
  AND e.record_id = ANY($2::bigint[])
  AND e.is_deleted = false
  AND s.keyword_status = 'COMPLETED'
"""

_INSERT = """
INSERT INTO ai.context_keyword (context_id, keyword_id, confidence, preset_version)
VALUES ($1, $2, $3, $4)
"""

_UPSERT_ANALYSIS = """
INSERT INTO ai.context_keyword_analysis
    (context_id, preset_version, unmatched_concepts, model_profile, updated_at)
VALUES ($1, $2, $3::jsonb, $4, now())
ON CONFLICT (context_id) DO UPDATE SET
    preset_version = EXCLUDED.preset_version,
    unmatched_concepts = EXCLUDED.unmatched_concepts,
    model_profile = EXCLUDED.model_profile,
    updated_at = now()
"""


async def replace(
    conn: asyncpg.Connection,
    context_id: int,
    selections: list[tuple[int, float | None]],
    preset_version: int,
) -> None:
    """기존 Keyword 전량 삭제 후 재삽입. selections는 (keyword_id, confidence). 0건 허용."""
    await conn.execute(_DELETE, context_id)
    if selections:
        await conn.executemany(
            _INSERT,
            [
                (context_id, kid, conf, preset_version)
                for kid, conf in selections
            ],
        )


async def keywords_for_records(
    conn: asyncpg.Connection,
    user_id: int,
    record_ids: list[int],
) -> list:
    """컷 통과 후보 Record 들의 (record_id, keyword_id) 신호 행 (S15P11A705-339).

    `keyword_status = COMPLETED` 인 Context 의 keyword 만 나온다. 조건의 의미와
    visibility 처리 위치는 위 `_KEYWORDS_FOR_RECORDS` 주석에 있다.
    """
    if not record_ids:
        return []
    return await conn.fetch(_KEYWORDS_FOR_RECORDS, user_id, record_ids)


async def upsert_analysis(
    conn: asyncpg.Connection,
    context_id: int,
    preset_version: int,
    unmatched_concepts: list[str],
    model_profile: str,
) -> None:
    """unmatchedConcepts 기록. 비어 있어도 행은 남긴다(분석 데이터)."""
    await conn.execute(
        _UPSERT_ANALYSIS,
        context_id,
        preset_version,
        json.dumps(unmatched_concepts, ensure_ascii=False),
        model_profile,
    )

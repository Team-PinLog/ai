> 현재 코드가 없는 구현 예정 명세입니다.
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# 개인 자연어 검색

근거 계약: `static/05_AI_설계.md` §9 개인 자연어 검색, §13.2 개인 검색

## 1. 엔드포인트

```text
POST /internal/v1/search
```

요청값: `userId`, `query`, `limit`, `embeddingProfile`
응답값: `recordId`, `similarity` 목록

`userId`는 필수이며 **검색 범위 필터로만** 사용합니다. FastAPI는 인증을 판단하지 않습니다.
반환된 Record ID는 `ai` 스키마 기준 결과이므로 소유권·삭제 여부·활성 Context 존재 여부는
Spring이 Core 기준으로 다시 검증합니다(계약 §9.5).

## 2. 질의 Embedding

질의는 **분해하지 않고 전체를 한 번** Embedding합니다.

- 검색어 LLM 분해를 하지 않습니다.
- 독립 Place 후보 검색, 독립 Keyword 후보 검색을 하지 않습니다.
- Embedding 호출은 요청당 정확히 1회입니다.

요청의 `embeddingProfile`이 서버 설정 Profile과 다르면 질의 벡터를 저장된 벡터와 비교할 수
없으므로 검색을 수행하지 않고 요청을 거부합니다([model-profile.md](model-profile.md)).

## 3. 필터 우선, 벡터 나중

MVP는 **정확 cosine 검색**을 사용합니다.

> **HNSW와 IVFFlat을 사용하지 않습니다.** ANN 인덱스는 MVP 제외 범위이며,
> 데이터 증가 이후의 확장 항목입니다(계약 §9.4, §15.2, §15.3).

정확 검색이므로 후보 행 수가 곧 비용입니다. 따라서 벡터 연산 전에 스칼라 조건으로 후보를
최대한 좁힙니다. `user_id`가 가장 강한 필터이며, `ai.context_embedding`이
`user_id`·`record_id`를 비정규화해 들고 있는 이유가 이것입니다.

```text
user_id 일치
→ is_deleted = false
→ embedding_status = COMPLETED
→ Embedding Profile 일치
→ (여기까지 좁힌 뒤) exact cosine 계산
```

필터 목록은 계약 §9.3과 동일합니다. Context가 불변이므로 본문 버전을 대조하는 조건은 없습니다.

ANN 인덱스가 없으므로 순서를 바꾸면 사용자 전체 벡터를 스캔하게 됩니다.
Query를 작성할 때 필터 조건이 벡터 연산 아래로 내려가지 않도록, 필터를 CTE로 분리하거나
서브쿼리에 고정합니다.

## 4. Query

```sql
WITH candidate AS (
    SELECT e.record_id,
           e.embedding
    FROM ai.context_embedding e
    JOIN ai.context_ai_state s
      ON s.context_id = e.context_id
    WHERE e.user_id = :user_id
      AND e.is_deleted = false
      AND e.embedding_profile = :embedding_profile
      AND s.embedding_status = 'COMPLETED'
),
scored AS (
    SELECT record_id,
           1 - (embedding <=> :query_embedding) AS similarity
    FROM candidate
)
SELECT record_id,
       MAX(similarity) AS similarity
FROM scored
GROUP BY record_id
ORDER BY similarity DESC
LIMIT :limit;
```

조건별 역할:

| 조건 | 역할 |
|---|---|
| `e.user_id = :user_id` | 검색 범위를 본인 Context로 한정. 타인 데이터 차단(검증 시나리오 19) |
| `e.is_deleted = false` | 삭제된 AI 파생 데이터 제외. Spring만 이 값을 변경 |
| `s.embedding_status = 'COMPLETED'` | 미완료·실패·CANCELLED 제외 |
| `e.embedding_profile = :embedding_profile` | 차원·거리 기준이 다른 벡터 제외 |

`<=>`는 pgvector의 cosine distance 연산자이며, 유사도는 `1 - distance`로 환산합니다.
거리 기준을 cosine으로 고정하는 근거는 Embedding Profile입니다.

`CANCELLED` 제외는 `embedding_status = 'COMPLETED'` 조건에 이미 포함됩니다.
`is_deleted`와 CANCELLED는 서로를 대체하지 않는 두 개의 방어선이므로 두 조건을 모두 유지합니다.

### 수정으로 대체된 구 Context

Context 수정은 구 Context 삭제와 신 Context 생성의 조합이므로(계약 §4.2, §5.3),
구 Context는 위 두 조건 **모두**에 걸려 검색에서 제외됩니다.

```text
구 Context: is_deleted = true  AND  embedding_status = CANCELLED
신 Context: 새 context_id로 별도 행이 생기고, 자신의 처리가 끝나면 검색 대상이 됨
```

본문 버전을 대조하는 조건은 두지 않습니다. 같은 `context_id`에 두 가지 본문이 존재할 수
없으므로 검사할 대상이 없습니다(검증 시나리오 3).

## 5. Record 단위 집계

유사도는 Context 단위로 계산하고 사용자에게는 **Record 단위**로 반환합니다.

- `GROUP BY record_id`로 중복을 제거합니다. 한 Record의 여러 Context가 매칭되어도
  Record는 한 번만 반환됩니다(검증 시나리오 20).
- Record 유사도는 그 Record에 속한 Context 유사도 중 **최댓값**을 사용합니다.
  평균이나 합계를 쓰지 않습니다. Context는 서로 독립적인 저장 이유이므로,
  하나만 강하게 일치해도 그 Record는 사용자가 찾는 대상입니다.
- `LIMIT`은 집계 **후에** 적용합니다. 집계 전에 자르면 서로 다른 Record 수가
  요청한 `limit`보다 적게 나올 수 있습니다.

## 6. 반환 형식

```json
{
  "results": [
    { "recordId": 1024, "similarity": 0.8213 },
    { "recordId": 993,  "similarity": 0.7740 }
  ]
}
```

- Context ID와 Context 본문을 반환하지 않습니다. Context 원문은 개인 데이터이며,
  응답 조립은 Spring의 책임입니다.
- Keyword를 함께 반환하지 않습니다. Keyword Visibility에 따른 노출 판단은 Spring이 합니다.
- 유사도 하한 컷오프는 서버 설정값으로 두되 기본은 적용하지 않습니다.
  최종 노출 여부는 Spring이 판단하므로, FastAPI가 임의로 결과를 지우지 않습니다.

## 7. 하지 않는 것

- HNSW / IVFFlat 인덱스 생성 (인덱스는 back의 migration 소유이기도 합니다)
- 검색어 LLM 분해
- 타인 Context·Collection 검색
- Place 이름 검색·지도 검색 (카카오맵 장소 검색 기능으로 별도 유지)
- Record 삭제 여부와 소유권 판단 (Spring의 Core 재검증)

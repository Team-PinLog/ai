> 현재 코드가 없는 구현 예정 명세입니다.
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# 삭제·수정 경합 제어

근거 계약: `static/05_AI_설계.md` §4.2 Context 불변성, §6.6 결과 저장 불변식, §11 삭제와 경합 방어

## 1. 무엇을 막는가

FastAPI가 Context를 처리하는 동안 Spring은 그 Context를 삭제할 수 있습니다.
모델 호출은 수 초가 걸리므로 이 창은 항상 열려 있습니다.

```text
FastAPI: 처리 시작 ─────── Embedding API 호출 ─────── 저장 시도
Spring:          └ Context 삭제 → 두 status CANCELLED ┘
```

이때 결과가 저장되면 이미 삭제된 Context의 벡터와 Keyword가 되살아나 검색·조회에 노출됩니다.
경합 제어는 이 저장을 막는 장치입니다.

## 2. 수정도 삭제 경합이다

Context는 불변 엔티티입니다. 본문이 한 글자라도 바뀌면 **새 `context_id`를 가진 별개의
Context**가 되고, 구 Context는 소프트 삭제됩니다(계약 §4.2, §5.3).

```text
구 Context 소프트 삭제
→ 구 embedding_status = CANCELLED
→ 구 keyword_status   = CANCELLED
→ 존재하는 구 Embedding is_deleted = true
→ 새 Context INSERT → 새 context_id → 새 AI State PENDING
```

따라서 **수정 경합을 위한 별도 방어 장치가 없습니다.** 수정 경합은 위 흐름을 통해
전부 삭제 경합으로 흡수됩니다. 이는 방어를 약화한 것이 아니라, 방어해야 할
"동일 ID의 가변 상태" 자체를 제거한 것입니다.

핵심 불변식:

```text
동일한 context_id는 항상 동일한 Context 본문을 의미한다.
```

이 불변식이 성립하므로 구현에 Context Version 개념이 존재하지 않습니다.
버전 비교, 버전 불일치 처리, State와 Embedding 행의 버전 대조는 모두 없습니다.
FastAPI가 `context_id`로 State를 찾았다면, 그 State는 요청이 들고 온 바로 그 본문에
대한 State입니다.

### 2.1 같은 context_id에 다른 text가 오면

계약 위반입니다(계약 §13.1). FastAPI는 이를 정상적인 Context 수정으로 처리하지 않습니다.

- 재생성·재판정으로 승격하지 않습니다.
- State를 되돌리거나 이미 저장된 결과를 무효화하지 않습니다.
- 상태 기계의 통상 경로대로만 처리합니다. 이미 `COMPLETED`이면 시작 전이에 실패해
  아무 일도 일어나지 않고, `PENDING`이면 도착한 본문으로 그대로 처리됩니다.
- 이 상황은 Spring 호출부의 결함이므로 `WARN` 이상으로 로그를 남겨 드러냅니다.

정상적인 Context 수정은 **반드시 새 `context_id`로 도착합니다.**

## 3. 두 번의 검사

같은 상태 검사를 두 곳에서 합니다. 목적이 다르므로 하나로 합칠 수 없습니다.

| | 사전 검사 | 저장 직전 검사 |
|---|---|---|
| 시점 | 모델 호출 전 | 결과 저장 직전 |
| 잠금 | 없음 | `SELECT ... FOR UPDATE` |
| 목적 | 불필요한 API 비용 차단 | 정합성 보장 |
| 놓치면 | 돈이 샌다 | 데이터가 깨진다 |
| 생략 가능? | 기능상 가능(비용 증가) | 불가 |

### 3.1 사전 검사 (cheap pre-check)

```sql
SELECT embedding_status, keyword_status
FROM ai.context_ai_state
WHERE context_id = :context_id;
```

- 잠금을 잡지 않습니다. 이 조회 결과는 조회 시점의 스냅샷일 뿐이며 어떤 것도 보장하지 않습니다.
- 통과하지 못하면 모델을 호출하지 않고 종료합니다. 이미 `CANCELLED`이거나 진행 가능한 단계가
  없는 Context에 Embedding·LLM 비용을 쓰지 않기 위한 것입니다.
- 통과했다고 해서 저장이 허용된 것은 아닙니다. 여기서의 통과는 "지금 시작할 만하다"는
  뜻 이상이 아닙니다.

이 검사만으로 정합성을 보장하려는 시도(조회 후 애플리케이션에서 판단하고 그대로 저장)는
전형적인 TOCTOU입니다. 조회와 저장 사이에 Spring의 삭제 트랜잭션이 끼어들 수 있습니다.

### 3.2 저장 직전 검사 (FOR UPDATE)

결과 저장과 **같은 트랜잭션 안에서** 잠금을 잡고 다시 검사합니다.

```sql
BEGIN;

SELECT embedding_status, keyword_status
FROM ai.context_ai_state
WHERE context_id = :context_id
FOR UPDATE;

-- embedding_status == 'PROCESSING' 확인
-- 실패 시 ROLLBACK, 결과 폐기

INSERT INTO ai.context_embedding (...)
ON CONFLICT (context_id) DO UPDATE SET ...;   -- is_deleted 제외

UPDATE ai.context_ai_state
SET embedding_status = 'COMPLETED', updated_at = now()
WHERE context_id = :context_id
  AND embedding_status = 'PROCESSING';

COMMIT;
```

`FOR UPDATE`가 하는 일은 검사 시점부터 커밋까지 그 행을 다른 트랜잭션이 바꾸지 못하게
막는 것입니다. Spring의 삭제·수정 트랜잭션(→ 구 Context `CANCELLED`)은 이 잠금 뒤에서
대기하고, 커밋 후에 자신의 변경을 적용합니다.

저장 불변식은 계약 §6.6 그대로이며, 대상 단계의 status 하나로 표현됩니다.

| 저장 대상 | 조건 |
|---|---|
| Embedding | `embedding_status = PROCESSING` |
| Keyword | `embedding_status = COMPLETED` AND `keyword_status = PROCESSING` |

조건을 만족하지 않으면 결과를 폐기합니다. 부분 저장은 없습니다.

Context가 불변이고 `context_id`가 본문 정체성을 나타내므로, 여기에 별도의 Version 검사를
덧붙이지 않습니다. 검사할 값이 하나 줄어든 것이지 방어선이 줄어든 것이 아닙니다.

### 3.3 잠금 구간에 넣지 않는 것

`FOR UPDATE` 이후 `COMMIT` 사이에는 SQL만 둡니다.

- Embedding API 호출 금지
- LLM API 호출 금지
- Preset Cache 재적재 금지
- 로깅 외의 네트워크 I/O 금지

모델 호출을 잠금 안에 넣으면 그 수 초 동안 Spring의 Context 삭제·수정 트랜잭션과
`FOR UPDATE SKIP LOCKED` 기반 재스캔이 함께 지연됩니다.
세션·트랜잭션 경계 규칙은 [architecture.md](architecture.md) §5를 참조합니다.

## 4. CANCELLED 우선

`CANCELLED`는 다른 모든 상태와 처리보다 우선합니다(계약 §11.1).

| 대상 | `CANCELLED`가 이기는 방식 |
|---|---|
| `PENDING` | 시작 전이 WHERE 조건에 걸려 작업이 시작되지 않음 |
| `PROCESSING` | 저장 직전 `FOR UPDATE` 검사에서 결과 폐기 |
| `COMPLETED` | 이미 저장된 결과가 검색·Keyword 조회 조건에서 제외됨 |
| `FAILED` | FastAPI의 FAILED 전이 WHERE 절에 `PROCESSING` 조건이 있어 덮어쓰지 못함 |
| Scheduler Finalizer | Spring이 `CANCELLED`를 `FAILED`로 덮어쓰지 않음(계약 §10.4) |

FastAPI 쪽에서 이를 성립시키는 규칙은 하나입니다.
**모든 상태 변경 UPDATE에 "현재 이 단계가 기대 상태인가" 조건을 남깁니다.**
조건을 남기면 `CANCELLED`가 자동으로 이깁니다. 애플리케이션 코드에
`if status == CANCELLED` 분기를 따로 두지 않습니다.

## 5. Embedding Row가 없는 경우

삭제·수정 시점에 Embedding Row가 아직 없으면 Spring의 `is_deleted = true` UPDATE는
영향 행 수 0으로 끝납니다. 이는 정상입니다(계약 §5.4, §11.2).

이 경우 걸어 둘 삭제 마커가 없으므로, 뒤늦게 도착하는 Embedding INSERT를 막는 것은
**State의 `CANCELLED`뿐**입니다.

```text
Spring: Context 삭제 → CANCELLED (Embedding Row 없음, is_deleted를 걸 대상 없음)
FastAPI: Embedding API 응답 도착
       → FOR UPDATE 재검사: embedding_status != PROCESSING
       → INSERT 하지 않고 롤백
```

따라서 저장 직전 검사를 "Embedding Row가 이미 있는지"나 `is_deleted` 조회로 대체할 수
없습니다. 검사 대상은 언제나 `ai.context_ai_state`의 status입니다.

## 6. 늦은 결과의 종착점

### 6.1 삭제

```text
FastAPI 처리 시작 (PROCESSING)
→ Spring이 Context 삭제 → 두 status CANCELLED
→ 존재하는 Embedding is_deleted = true
→ FastAPI 결과 도착
→ FOR UPDATE 재검사: status != PROCESSING
→ 결과 폐기, 롤백
```

### 6.2 수정

```text
FastAPI 구 Context 처리 시작 (PROCESSING)
→ 사용자가 Context 수정
→ Spring이 구 Context CANCELLED + is_deleted
→ Spring이 신 Context INSERT (새 context_id, 새 State PENDING)
→ FastAPI 구 결과 도착 → 저장 거부
→ 신 context_id 요청이 별도로 도착해 독립 처리
```

두 경우 모두 **부작용 없이 아무것도 쓰지 않고 종료**합니다.
구 Context의 처리 결과나 상태가 신 Context로 승계되는 경로는 존재하지 않습니다.

검증 시나리오 1, 2, 6, 10, 11, 12, 18이 이 경로를 검증합니다([integration-tests.md](integration-tests.md)).

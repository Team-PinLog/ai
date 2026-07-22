> 현재 코드가 없는 구현 예정 명세입니다.
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# Version 경합 제어

근거 계약: `static/05_AI_설계.md` §6.1 Context Version, §6.5 결과 저장 불변식, §11.3 늦은 결과

## 1. 무엇을 막는가

FastAPI가 Context를 처리하는 동안 Spring은 그 Context를 수정하거나 삭제할 수 있습니다.
모델 호출은 수 초가 걸리므로 이 창은 항상 열려 있습니다.

```text
FastAPI: v1 처리 시작 ─────── Embedding API 호출 ─────── 저장 시도
Spring:            └ Context 수정 → v2, 상태 PENDING ┘
```

이때 v1 결과가 저장되면 v2 본문과 v1 벡터가 어긋난 채로 검색에 노출됩니다.
Version 경합 제어는 이 저장을 막는 장치입니다.

## 2. `!=` 비교

Version 비교는 `<`가 아니라 **`!=`** 입니다.

```python
if request.context_version != state.context_version:
    return  # 처리하지 않는다
```

`<`를 쓰지 않는 이유:

- **더 큰 Version 요청도 잘못된 요청입니다.** State가 아직 갱신되지 않았거나 Spring이
  다른 Context의 값을 보낸 경우인데, `<` 비교는 이를 통과시켜 State와 다른 버전의 결과를
  저장합니다.
- `ai.context_ai_state.context_version`은 **Core Version의 복제값**이지 AI가 증가시키는
  값이 아닙니다. 두 값의 관계는 순서가 아니라 **동일성**입니다.
- Version의 원본은 `core.context.body_version`이며 본문이 실제로 바뀔 때만 증가합니다.
  JPA 낙관적 락 버전과는 다른 값이므로, "더 크면 최신"이라는 가정을 코드에 넣을 근거가 없습니다.

즉 판단은 "구버전인가"가 아니라 **"State가 기대하는 그 버전인가"** 입니다.
불일치는 방향과 무관하게 전부 폐기 사유입니다.

## 3. 두 번의 검사

같은 비교를 두 곳에서 합니다. 목적이 다르므로 하나로 합칠 수 없습니다.

| | 사전 검사 | 저장 직전 검사 |
|---|---|---|
| 시점 | 모델 호출 전 | 결과 저장 직전 |
| 잠금 | 없음 | `SELECT ... FOR UPDATE` |
| 목적 | 불필요한 API 비용 차단 | 정합성 보장 |
| 놓치면 | 돈이 샌다 | 데이터가 깨진다 |
| 생략 가능? | 기능상 가능(비용 증가) | 불가 |

### 3.1 사전 검사 (cheap pre-check)

```sql
SELECT context_version, embedding_status, keyword_status
FROM ai.context_ai_state
WHERE context_id = :context_id;
```

- 잠금을 잡지 않습니다. 이 조회 결과는 조회 시점의 스냅샷일 뿐이며 어떤 것도 보장하지 않습니다.
- 통과하지 못하면 모델을 호출하지 않고 종료합니다. 이미 CANCELLED이거나 구버전 요청인
  Context에 Embedding·LLM 비용을 쓰지 않기 위한 것입니다.
- 통과했다고 해서 저장이 허용된 것은 아닙니다. 여기서의 통과는 "지금 시작할 만하다"는
  뜻 이상이 아닙니다.

이 검사만으로 정합성을 보장하려는 시도(조회 후 애플리케이션에서 비교하고 그대로 저장)는
전형적인 TOCTOU입니다. 조회와 저장 사이에 Spring 트랜잭션이 끼어들 수 있습니다.

### 3.2 저장 직전 검사 (FOR UPDATE)

결과 저장과 **같은 트랜잭션 안에서** 잠금을 잡고 다시 검사합니다.

```sql
BEGIN;

SELECT context_version, embedding_status
FROM ai.context_ai_state
WHERE context_id = :context_id
FOR UPDATE;

-- context_version == request_version AND embedding_status == 'PROCESSING' 확인
-- 실패 시 ROLLBACK, 결과 폐기

INSERT INTO ai.context_embedding (...)
ON CONFLICT (context_id) DO UPDATE SET ...;   -- is_deleted 제외

UPDATE ai.context_ai_state
SET embedding_status = 'COMPLETED', updated_at = now()
WHERE context_id = :context_id
  AND context_version = :request_version
  AND embedding_status = 'PROCESSING';

COMMIT;
```

`FOR UPDATE`가 하는 일은 검사 시점부터 커밋까지 그 행을 다른 트랜잭션이 바꾸지 못하게
막는 것입니다. Spring의 삭제(→ CANCELLED)나 수정(→ Version 증가 + PENDING) 트랜잭션은
이 잠금 뒤에서 대기하고, 커밋 후에 자신의 변경을 적용합니다.

저장 불변식은 계약 §6.5 그대로입니다.

```text
request.contextVersion == state.context_version
AND 대상 단계 status == PROCESSING
```

둘 중 하나라도 어긋나면 결과를 폐기합니다. 부분 저장은 없습니다.

### 3.3 잠금 구간에 넣지 않는 것

`FOR UPDATE` 이후 `COMMIT` 사이에는 SQL만 둡니다.

- Embedding API 호출 금지
- LLM API 호출 금지
- Preset Cache 재적재 금지
- 로깅 외의 네트워크 I/O 금지

모델 호출을 잠금 안에 넣으면 그 수 초 동안 Spring의 Context 삭제·수정 트랜잭션과
`FOR UPDATE SKIP LOCKED` 기반 재스캔이 함께 지연됩니다.
세션·트랜잭션 경계 규칙은 [architecture.md](architecture.md) §5를 참조합니다.

## 4. Embedding 재사용 시의 Version

이미 저장된 Embedding을 Keyword 단계에서 재사용할 때도 같은 비교를 적용합니다.

```text
context_embedding.context_version == request.contextVersion
AND context_embedding.embedding_profile == 현재 Profile
AND state.embedding_status == 'COMPLETED'
AND state.context_version == request.contextVersion
```

Embedding 행 자체의 `context_version`을 검사해야 하는 이유는, State가 최신이더라도
Embedding 행이 아직 갱신되지 않은 중간 상태가 존재할 수 있기 때문입니다.
상세는 [partial-resume.md](partial-resume.md).

## 5. 늦은 결과의 종착점

```text
FastAPI 처리 시작 (v1, PROCESSING)
→ Spring이 Context 삭제 → 두 status CANCELLED
→ FastAPI 결과 도착
→ FOR UPDATE 재검사: status != PROCESSING
→ 결과 폐기, 롤백
```

수정의 경우도 동일합니다. `context_version`이 v2로 올라가 있으므로 v1 결과는
Version 비교에서 폐기됩니다.

두 경우 모두 **부작용 없이 아무것도 쓰지 않고 종료**합니다.
검증 시나리오 1, 7, 8, 12가 이 경로를 검증합니다([integration-tests.md](integration-tests.md)).

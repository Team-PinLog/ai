> 구현 반영됨(ai#5·#6, `app/`). 이 문서는 계약 명세이며 구현이 이를 따른다. 리포트: [implements/2026-07-23-fastapi-implementation.md](../implements/2026-07-23-fastapi-implementation.md).
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# State Machine 구현

근거 계약: `static/05_AI_설계.md` §6.1 상태 컬럼, §6.2 상태값, §6.3 상태 전이, §6.4 상태 쓰기 책임, §12.2 context_ai_state

## 1. 두 개의 독립 상태

`ai.context_ai_state`에는 통합 status가 없습니다. 두 컬럼이 **같은 상태 집합을 독립적으로**
사용합니다.

| 컬럼 | 담당 단계 |
|---|---|
| `embedding_status` | Embedding 생성·저장 |
| `keyword_status` | Preset 후보 검색과 LLM 판정·저장 |

구현에서 지켜야 할 결론:

- 두 컬럼을 한 번의 UPDATE로 함께 바꾸지 않습니다. 단계별로 각각 조건부 UPDATE합니다.
- 조합 상태를 "이상 상태"로 보지 않습니다. `embedding=COMPLETED / keyword=PENDING`은
  정상이며 부분 재개의 기반입니다([partial-resume.md](partial-resume.md)).
- 코드에서 상태를 다룰 때 `(context_id, stage)` 쌍을 키로 취급합니다.
  `stage`는 `EMBEDDING` / `KEYWORD` 두 값을 가지는 열거형이며, 이 값이 조건부 UPDATE의
  대상 컬럼명을 결정합니다.

```python
class Stage(StrEnum):
    EMBEDDING = "embedding_status"
    KEYWORD = "keyword_status"
```

컬럼명을 문자열로 조립하는 경로가 생기므로, 열거형 값 외의 문자열이 SQL로 들어가지 못하도록
repository 진입점에서 `Stage` 타입만 받습니다.

## 2. FastAPI가 수행할 수 있는 전이

```text
PENDING     → PROCESSING     (작업 시작)
PROCESSING  → PROCESSING     (만료된 stale 작업 재선점, updated_at 갱신)
PROCESSING  → COMPLETED      (결과 저장 성공)
PROCESSING  → FAILED         (작업 중 발생한 영구 오류)
```

FastAPI가 **절대 수행하지 않는** 전이:

| 전이 | 주체 | 이유 |
|---|---|---|
| `* → PENDING` | Spring | AI State 최초 생성 시에만 부여됩니다. 기존 State를 PENDING으로 되돌리는 전이는 설계에 존재하지 않습니다 |
| `* → CANCELLED` | Spring | Context 삭제·수정·회원 탈퇴의 결과. FastAPI는 삭제 사실을 알 수 없음 |
| `FAILED → PROCESSING` | 없음 | 계약상 금지. `FAILED`는 진짜 종결 상태이며 같은 Context를 다시 처리하는 경로가 없음 |
| `COMPLETED → *` | 없음(FastAPI 기준) | 완료된 단계를 FastAPI가 되돌리지 않음 |
| 재시도 소진 `FAILED` | Spring | Finalizer의 판단. FastAPI는 `retry_count` 소진 여부를 모름 |

계약 §6.4의 상태 쓰기 책임을 그대로 옮기면 다음과 같습니다.

| 상태 변경 | Spring | FastAPI |
|---|:---:|:---:|
| AI State 최초 생성 / `PENDING` | O | X |
| `PROCESSING` | X | O |
| `COMPLETED` | X | O |
| 작업 중 명시적 오류 `FAILED` | X | O |
| 재시도 소진 Finalizer `FAILED` | O | X |
| `CANCELLED` | O | X |
| `retry_count` | O | X |
| `is_deleted` | O | X |

`FAILED`를 FastAPI가 설정하는 경우는 **작업 중 발생한, 재시도해도 결과가 달라지지 않는
영구 오류**뿐입니다. 일시적 오류에서는 상태를 건드리지 않고 PROCESSING으로 둔 채 종료하여,
만료 후 Spring 재스캔의 재요청과 §3.1의 재선점으로 회수되게 합니다. 판단 기준은
[failure-recovery.md](failure-recovery.md)를 따릅니다.

`retry_count`는 FastAPI가 읽지도 쓰지도 않습니다. 재시도 횟수 관리는 Spring의 책임입니다.

### 2.1 Context 수정은 상태 전이가 아니다

Context는 불변 엔티티이므로 본문 수정이 기존 State를 되살리지 않습니다(계약 §4.2, §6.3).

```text
구 Context: 두 status → CANCELLED  (종결)
신 Context: 새 context_id, 새 State, 두 status = PENDING  (별개의 처리 단위)
```

따라서 다음이 성립합니다.

- `FAILED`는 진짜 종결 상태입니다. 본문이 수정되어도 그 State는 다시 살아나지 않습니다.
  살아나는 것은 **다른 `context_id`의 새 State**입니다.
- 기존 State를 `PENDING`으로 초기화하는 경로가 없으므로, 그 전이를 가정한 분기를
  코드에 두지 않습니다.
- 구 Context의 `retry_count`, `COMPLETED`, `FAILED`는 신 Context로 승계되지 않습니다.

## 3. 조건부 UPDATE

### 3.1 시작 전이

```sql
UPDATE ai.context_ai_state
SET embedding_status = 'PROCESSING',
    updated_at = now()
WHERE context_id = :context_id
  AND embedding_status IN ('PENDING', 'PROCESSING')
  AND (embedding_status = 'PENDING'
       OR updated_at < now() - :processing_expiry);
```

Keyword 단계는 여기에 `embedding_status = 'COMPLETED'` 조건을 더하고 대상 컬럼만 바꿉니다
(계약 §6.5).

WHERE 절이 각각 막는 것:

| 조건 | 막는 상황 |
|---|---|
| `context_id` | 대상 지정 |
| `status IN ('PENDING', 'PROCESSING')` | CANCELLED, 이미 COMPLETED, FAILED에서의 직접 재개 |
| `PENDING이거나 만료된 PROCESSING` | 살아 있는 작업의 중복 실행 |

`status IN ('PENDING', 'PROCESSING')`이 계약 §6.5의 허용 집합입니다. `PROCESSING`이
포함된 이유는 **중단된 stale 작업의 재개** 때문입니다. Context가 불변이 되면서 기존 State를
`PENDING`으로 되돌리는 경로가 사라졌으므로, 프로세스가 죽어 `PROCESSING`으로 남은 작업은
이 조건으로만 회수됩니다(검증 시나리오 8).

만료 조건은 그 허용 집합을 **stale한 PROCESSING으로 좁히는** ai 레포의 구현 규칙입니다.
이것이 없으면 동시에 도착한 중복 요청 두 개가 모두 전이에 성공해 모델을 두 번 호출합니다
(계약 §13.1 멱등성). 만료 기준은 Spring 재스캔의 `PROCESSING` 만료(10분)와 같은 값을
설정으로 주입받으며, ai 레포가 독자적인 값을 정하지 않습니다.

`FAILED → PROCESSING` 금지, 삭제·수정된 구 Context의 `CANCELLED` 차단은 전부 이 WHERE 절이
처리합니다. 애플리케이션 코드에서 `if status == FAILED` 같은 분기를 추가로 두지 않습니다.
분기는 조회와 UPDATE 사이의 시간 간격만큼 틀릴 수 있지만, WHERE 절은 틀릴 수 없습니다.

Context 수정에 대한 방어를 이 WHERE 절에 추가하지 않습니다. 수정은 구 Context를
`CANCELLED`로 만들고 신 Context를 새 `context_id`로 만들기 때문에, 이미 있는
status 조건이 그대로 방어선이 됩니다([deletion-race-control.md](deletion-race-control.md) §2).

### 3.2 영향 행 수 0이면 중단

```python
affected = await ai_state_repo.try_start(session, context_id, stage)
if affected == 0:
    return  # 이 단계를 시작하지 않는다. 예외가 아니다.
```

규칙:

- **영향 행 수 0은 정상 종료입니다.** 예외를 던지거나 에러 로그 레벨로 기록하지 않습니다.
  정상 경합의 결과이며, 검증 시나리오 6(수정 후 구 `context_id` 재처리 요청),
  9(중복 요청), 18(재스캔 후보 선택 후 삭제)의 기대 동작입니다.
- repository는 `rowcount`를 그대로 반환하고, 중단 여부는 service가 판단합니다.
- 0인 이유를 알기 위해 다시 SELECT하지 않습니다. 이유를 알아도 할 일이 달라지지 않습니다.
- Embedding 단계가 0으로 중단되어도 Keyword 단계 시도는 별도로 판단합니다.
  Embedding이 이미 COMPLETED여서 0이 나온 경우가 정확히 부분 재개 경로입니다.

### 3.3 완료 전이

완료 전이는 단독 UPDATE가 아니라 저장 트랜잭션 안에서 수행합니다.

```sql
-- 같은 트랜잭션: FOR UPDATE 재검사 → 결과 저장 → 완료 전이
UPDATE ai.context_ai_state
SET keyword_status = 'COMPLETED',
    updated_at = now()
WHERE context_id = :context_id
  AND embedding_status = 'COMPLETED'
  AND keyword_status = 'PROCESSING';
```

여기서도 WHERE 조건을 유지합니다. `FOR UPDATE`로 이미 잠갔더라도 조건을 빼지 않습니다.
잠금 획득과 조건 검증은 다른 일이며, 조건을 남겨 두면 검증 누락이 데이터 오염이 아니라
영향 행 수 0으로 드러납니다.

### 3.4 실패 전이

```sql
UPDATE ai.context_ai_state
SET embedding_status = 'FAILED',
    updated_at = now()
WHERE context_id = :context_id
  AND embedding_status = 'PROCESSING';
```

`PROCESSING` 조건이 있으므로, 실패 처리 중에 Context가 삭제·수정되어 CANCELLED가 되었다면
FAILED로 덮어쓰지 않습니다. CANCELLED가 유지되어야 재스캔 대상에서 계속 제외됩니다.
`CANCELLED` 우선 규칙(계약 §11.1)이 코드 분기가 아니라 WHERE 절로 강제되는 지점입니다.

## 4. updated_at

모든 상태 변경 UPDATE에 `updated_at = now()`를 포함합니다. 이 값이 Spring 재스캔의
PENDING 5분 / PROCESSING 10분 만료 판정 기준이므로, 갱신을 빠뜨리면 stale 작업이
조기에 회수되거나 영원히 회수되지 않습니다.

`updated_at` 갱신을 DB 트리거에 의존하지 않고 UPDATE 문에 명시합니다. 트리거는 back의
migration 소유이며 ai 레포가 그 존재를 가정할 수 없습니다.

## 5. 상태 조회는 계약이 아니다

Spring은 `ai.context_ai_state`를 직접 조회하여 상태를 파악합니다. FastAPI는 상태 조회 API를
제공하지 않으며, 완료 통보 웹훅도 두지 않습니다(계약 §13.1).

따라서 상태 컬럼의 값 자체가 파트 간 인터페이스입니다. 상태값 집합
(`PENDING` / `PROCESSING` / `COMPLETED` / `FAILED` / `CANCELLED`)에 새 값을 추가하거나
의미를 바꾸는 것은 ai 레포 단독으로 결정할 수 없으며, `static/05_AI_설계.md` §6.2의 변경을
거쳐야 합니다.

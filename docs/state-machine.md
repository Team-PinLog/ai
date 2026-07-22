> 현재 코드가 없는 구현 예정 명세입니다.
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# State Machine 구현

근거 계약: `static/05_AI_설계.md` §6.2 상태 컬럼, §6.3 상태값, §6.4 상태 전이, §12.2 context_ai_state

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
PROCESSING  → COMPLETED      (결과 저장 성공)
PROCESSING  → FAILED         (영구 오류)
```

FastAPI가 **절대 수행하지 않는** 전이:

| 전이 | 주체 | 이유 |
|---|---|---|
| `* → PENDING` | Spring | Context 본문 수정 시 초기화. FastAPI가 되돌리면 Version이 맞지 않는 재처리를 유발 |
| `* → CANCELLED` | Spring | Context 삭제·회원 탈퇴의 결과. FastAPI는 삭제 사실을 알 수 없음 |
| `FAILED → PROCESSING` | 없음 | 계약상 금지. FAILED는 자동 재스캔 대상이 아님 |
| `COMPLETED → *` | 없음(FastAPI 기준) | 완료된 단계를 FastAPI가 되돌리지 않음 |

`FAILED`를 FastAPI가 설정하는 경우는 **재시도해도 결과가 달라지지 않는 영구 오류**뿐입니다.
일시적 오류에서는 상태를 건드리지 않고 PROCESSING으로 둔 채 종료하여, 만료 후 Spring 재스캔이
회수하게 합니다. 판단 기준은 [failure-recovery.md](failure-recovery.md)를 따릅니다.

`retry_count`는 FastAPI가 읽지도 쓰지도 않습니다. 재시도 횟수 관리는 Spring의 책임입니다.

## 3. 조건부 UPDATE

### 3.1 시작 전이

```sql
UPDATE ai.context_ai_state
SET embedding_status = 'PROCESSING',
    updated_at = now()
WHERE context_id = :context_id
  AND context_version = :request_version
  AND embedding_status = 'PENDING';
```

WHERE 절의 세 조건이 각각 막는 것:

| 조건 | 막는 상황 |
|---|---|
| `context_id` | 대상 지정 |
| `context_version = :request_version` | 처리 시작 전에 Context가 수정된 경우 |
| `embedding_status = 'PENDING'` | 중복 실행, CANCELLED, 이미 COMPLETED, FAILED에서의 직접 재개 |

`embedding_status = 'PENDING'` 조건 하나가 `FAILED → PROCESSING` 금지와 중복 요청 흡수를
동시에 처리합니다. 애플리케이션 코드에서 `if status == FAILED` 같은 분기를 추가로 두지 않습니다.
분기는 조회와 UPDATE 사이의 시간 간격만큼 틀릴 수 있지만, WHERE 절은 틀릴 수 없습니다.

### 3.2 영향 행 수 0이면 중단

```python
affected = await ai_state_repo.try_start(session, context_id, version, stage)
if affected == 0:
    return  # 이 단계를 시작하지 않는다. 예외가 아니다.
```

규칙:

- **영향 행 수 0은 정상 종료입니다.** 예외를 던지거나 에러 로그 레벨로 기록하지 않습니다.
  정상 경합의 결과이며, 검증 시나리오 6(중복 요청)과 12(재스캔 후보 선택 후 삭제)의 기대 동작입니다.
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
  AND context_version = :request_version
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
  AND context_version = :request_version
  AND embedding_status = 'PROCESSING';
```

`PROCESSING` 조건이 있으므로, 실패 처리 중에 Context가 삭제되어 CANCELLED가 되었다면
FAILED로 덮어쓰지 않습니다. CANCELLED가 유지되어야 재스캔 대상에서 계속 제외됩니다.

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
의미를 바꾸는 것은 ai 레포 단독으로 결정할 수 없으며, `static/05_AI_설계.md` §6.3의 변경을
거쳐야 합니다.

> 구현 반영됨(ai#5·#6, `app/`). 이 문서는 계약 명세이며 구현이 이를 따른다. 리포트: [implements/2026-07-23-fastapi-implementation.md](../implements/2026-07-23-fastapi-implementation.md).
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# 오류 분류와 복구

근거 계약: `static/05_AI_설계.md` §10 실패 복구와 재시도, §6.3 상태 전이, §6.4 상태 쓰기 책임, §11 삭제와 경합 방어

## 1. 복구의 주체

AI State가 DB에 영속되므로 메시지 큐나 Outbox 없이 재처리가 가능합니다.
따라서 **복구의 주체는 FastAPI가 아니라 Spring Scheduler**입니다.

| 책임 | 주체 |
|---|---|
| 오류 분류 | FastAPI |
| 상태 반영 (PROCESSING 유지 / FAILED) | FastAPI |
| 재시도 횟수(`retry_count`) 관리 | Spring |
| 만료 판정과 재스캔 | Spring |
| Core Context 존재·삭제 여부 확인 후 재전달 | Spring |
| 최대 재시도 소진 시 Finalizer FAILED 종결 | Spring |

FastAPI가 무한 재시도하지 않는 이유:

- **재시도 상태가 프로세스 메모리에만 있으면 프로세스가 죽는 순간 사라집니다.**
  Spring이 관리하는 `retry_count`는 DB에 있어 재시작에도 살아남습니다.
- FastAPI가 자체 재시도하면 `retry_count`가 실제 시도 횟수를 반영하지 못해
  최대 3회 제한이 의미를 잃습니다. 비용 상한이 사라집니다.
- 재시도 시에는 **그 Context가 아직 삭제되지 않았는지 Core에서 확인**해야 하는데(계약 §10.3),
  FastAPI는 `core.*`에 접근할 수 없으므로 그 판단을 할 수 없습니다.
- 처리 중 Context가 삭제되었거나 수정으로 대체되었을 수 있고, 그 판단은 State 재조회가
  필요합니다. 재스캔 경로는 이미 그 검사를 포함합니다.
  Context가 불변이므로 재요청의 `text`는 최초 요청과 동일하며, FastAPI가 본문의 최신성을
  걱정할 일은 없습니다. 걱정해야 하는 것은 오직 **그 Context가 아직 살아 있는가**입니다.

예외는 **단일 호출 내부의 짧은 재시도**뿐입니다(§3.1).

## 2. 오류 분류

`app/core/errors.py`에서 두 종류로 분류합니다.

```python
class TransientError(Exception): ...   # 다시 시도하면 성공할 수 있음
class PermanentError(Exception): ...   # 다시 시도해도 같은 결과
```

### 2.1 일시적 오류 (Transient)

| 사례 | 비고 |
|---|---|
| 외부 API 타임아웃 | Embedding / LLM 공통 |
| `429 Too Many Requests` | Rate limit |
| `5xx` 응답 | 제공자 장애 |
| 네트워크 연결 실패·DNS 실패 | |
| DB 연결 실패, 잠금 타임아웃, 직렬화 실패 | |

**동작: 상태를 건드리지 않고 PROCESSING으로 둔 채 종료합니다.**

- `FAILED`로 내리지 않습니다. FAILED는 자동 재스캔 대상이 아니므로,
  일시 장애 하나가 그 Context를 영구히 죽입니다.
- `PENDING`으로 되돌리지 않습니다. FastAPI는 PENDING으로 전이할 권한이 없습니다
  ([state-machine.md](state-machine.md) §2).
- 결과적으로 PROCESSING 만료(10분) 후 Spring 재스캔이 회수합니다.
  즉시 회수되지 않는 대신, 상태 소유권 경계가 깨지지 않습니다.
- 로그는 `WARN`으로 남기고 `context_id`, `stage`, 원인 분류를 포함합니다.

### 2.2 영구 오류 (Permanent)

| 사례 | 비고 |
|---|---|
| `400` 계열 요청 오류 (모델명 오류, 입력 형식 오류) | 설정·코드 문제 |
| 인증 실패 (`401` / `403`) | 키 문제. 재시도해도 동일 |
| Embedding 응답 차원 불일치 | [model-profile.md](model-profile.md) §5 |
| Context Embedding과 Preset의 Profile 불일치 | 검증 시나리오 13 |
| LLM 구조화 출력이 재시도 후에도 스키마 위반 | |
| 입력 텍스트가 모델 최대 길이를 초과 | 본문이 바뀌기 전엔 동일 |

**동작: 해당 단계만 `PROCESSING → FAILED`로 전이합니다.**

```sql
UPDATE ai.context_ai_state
SET keyword_status = 'FAILED', updated_at = now()
WHERE context_id = :context_id
  AND keyword_status = 'PROCESSING';
```

- 다른 단계의 status는 건드리지 않습니다. Embedding이 COMPLETED이면 그대로 둡니다(계약 §10.4).
- 결과는 저장하지 않습니다. 부분 결과를 남기지 않습니다.
- 이 `FAILED`는 **FastAPI가 작업 중 판단한 명시적 오류**입니다. 재시도 소진으로 인한
  `FAILED`는 Spring Finalizer의 몫이며 FastAPI가 대신 기록하지 않습니다(계약 §6.4).
- `FAILED`는 **진짜 종결 상태**입니다. 자동으로 되살아나지 않으며, 같은 `context_id`를
  다시 처리하는 경로도 없습니다(계약 §6.3). 사용자가 본문을 고치면 그것은 새 `context_id`의
  새 State로 처리되며, 이 Context의 재개가 아닙니다.
- `keyword_status = 'PROCESSING'` 조건 덕분에 그 사이 Context가 삭제·수정되어 `CANCELLED`가
  되었다면 FAILED로 덮어쓰지 않습니다. `CANCELLED`가 우선합니다(계약 §11.1).
- 로그는 `ERROR`로 남깁니다. 영구 오류는 대부분 배포 설정 문제이므로 알림 대상입니다.

인증 실패나 모델명 오류처럼 **전 서비스에 영향을 주는 오류**를 개별 Context의 FAILED로
누적시키는 것은 바람직하지 않습니다. 같은 영구 오류가 짧은 시간에 임계치 이상 발생하면
Circuit Breaker를 열어 이후 요청을 즉시 중단시키고, 그 구간의 요청은 PROCESSING을
유지한 채 종료(일시적 오류 취급)합니다.

### 2.3 오류가 아닌 것

다음은 실패가 아니므로 상태를 바꾸지 않습니다.

| 상황 | 처리 |
|---|---|
| 조건부 UPDATE 영향 행 수 0 | 정상 종료. `INFO`/`DEBUG` 로그 |
| 저장 직전 status 검사 실패 | 결과 폐기 후 정상 종료. 늦은 결과의 정상 경로 |
| State 행 없음 | 정상 종료 |
| 삭제·수정으로 두 status가 `CANCELLED` | 정상 종료. 처리 대상 아님 |
| 선택된 Keyword 0개 | 정상 `COMPLETED` |
| 후보 TOP-K 결과 0개 | LLM 미호출, 정상 `COMPLETED` |

이들을 예외로 던져 에러 로그를 채우면 실제 장애를 가리게 됩니다.

예외는 하나입니다. **같은 `context_id`에 이전과 다른 `text`가 도착하는 것은 계약 위반**이며
(계약 §13.1), 정상 경로가 아니므로 `WARN` 이상으로 남겨 호출부의 결함을 드러냅니다.
다만 이 경우에도 예외를 던져 파이프라인을 비정상 종료시키지는 않습니다
([deletion-race-control.md](deletion-race-control.md) §2.1).

## 3. 호출 단위 방어

### 3.1 짧은 재시도

단일 API 호출 안에서만 제한적으로 재시도합니다.

- 대상: 타임아웃, `429`, `5xx`, 연결 실패
- 횟수: 최대 2회 (총 3회 시도)
- 간격: 지수 백오프 + jitter, 총 소요가 PROCESSING 만료(10분)보다 훨씬 짧게 유지
- 비대상: `4xx` 요청 오류, 인증 실패, 스키마 위반

이 재시도는 "네트워크 흔들림 흡수"이지 "복구"가 아닙니다. 소진되면 §2.1로 넘어가
Spring 재스캔에 맡깁니다.

### 3.2 타임아웃

- Embedding / LLM 클라이언트에 연결·읽기 타임아웃을 각각 설정합니다.
- 두 호출의 타임아웃 합 + 재시도 시간이 PROCESSING 만료(10분)를 넘지 않게 상한을 잡습니다.
  넘으면 재스캔이 아직 살아 있는 작업을 중복 실행하게 됩니다.
  중복 실행 자체는 조건부 UPDATE로 흡수되지만, 비용은 두 배가 됩니다.

### 3.3 프로세스 종료

PROCESSING 상태에서 프로세스가 죽으면 그 작업은 사라집니다.
FastAPI에는 이를 복구할 장치가 없고, 있을 필요도 없습니다.
`updated_at` 기준 10분 만료 후 Spring 재스캔이 회수합니다(검증 시나리오 8).

따라서 종료 시 상태를 정리하려 하지 않습니다. 강제 종료(`SIGKILL`)에서는 어차피 실행되지
않으므로, 정상 종료에서만 동작하는 정리 로직은 두 경로의 동작을 다르게 만들 뿐입니다.

## 4. Spring 재스캔과의 접점

Spring Scheduler 기준값 (계약 §10.3):

| 항목 | 값 |
|---|---|
| 실행 주기 | 5분 |
| PENDING 만료 | 5분 |
| PROCESSING 만료 | 10분 |
| 최대 재시도 | 3회 |
| Backoff | 없음 |
| 후보 잠금 | `FOR UPDATE SKIP LOCKED` |

FastAPI가 이 동작을 성립시키기 위해 지켜야 하는 것:

1. **모든 상태 변경에서 `updated_at`을 갱신합니다.** 만료 판정의 유일한 기준입니다.
2. **일시적 오류에서 PROCESSING을 유지합니다.** PENDING으로 되돌리면 5분 만료로 앞당겨지지만,
   상태 소유권 경계를 깨고 재시도 회계도 어긋납니다.
3. **재스캔 요청도 일반 요청과 동일하게 처리합니다.** 재스캔 전용 경로를 두지 않습니다.
   조건부 UPDATE가 stale 작업과 신규 작업을 구분 없이 처리합니다.
4. **재스캔 후보 선택 이후 Context가 삭제·수정된 경우**, 요청은 도착하지만 status가
   CANCELLED이므로 PROCESSING 전환에 실패하고 실행되지 않습니다(검증 시나리오 18).
   수정으로 만들어진 신 Context는 자신의 `PENDING` State로 별도 요청이 오며,
   구 Context의 `retry_count`를 물려받지 않습니다.
5. **재시도 소진 판단을 하지 않습니다.** `retry_count = 3`인 stale 작업을 `FAILED`로
   종결하는 것은 Spring Finalizer의 책임이며(계약 §6.4, §10.4, 검증 시나리오 16),
   FastAPI는 `CANCELLED`를 `FAILED`로 덮어쓰지 않는 WHERE 조건만 지킵니다
   (검증 시나리오 17).

## 5. 기본 기능에 대한 영향

AI 실패는 Place, Record, Collection 기본 기능을 중단시키지 않습니다.

- FastAPI가 완전히 죽어 있어도 Spring은 Core를 커밋하고 State를 PENDING으로 남깁니다.
- Keyword가 미완료이거나 실패하면 Keyword만 생략합니다(검증 시나리오 21).
- 검색 API 실패는 AI 자연어 검색 기능만 실패시키며, Place 이름 검색·지도 검색은 별개입니다.

FastAPI 쪽에서 이를 보장하는 규칙은 하나입니다.
**어떤 오류에서도 Core 상태를 바꾸려 하지 않고, `ai` 스키마 밖으로 나가지 않습니다.**

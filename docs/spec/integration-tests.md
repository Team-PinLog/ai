> 현재 코드가 없는 구현 예정 명세입니다.
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# AI 파트 통합 테스트

근거 계약: `static/05_AI_설계.md` §16 필수 검증 시나리오

## 1. 범위

계약 §16의 21개 시나리오 중 **AI 파트가 소유하는 것**을 이 레포에서 검증합니다.
소유하지 않는 시나리오도 표에 남겨 두어 어디서 검증되는지 추적할 수 있게 합니다.

- `AI` — ai 레포에서 검증. 이 문서가 테스트 정의를 소유합니다.
- `AI+` — 양쪽에서 검증. ai 레포는 `ai` 스키마 관점만 검증합니다.
- `BE` — back 레포 소유. ai 레포는 테스트를 두지 않습니다.

전제는 **Context 불변성**입니다(계약 §4.2). 수정은 구 Context 삭제와 신 Context 생성의
조합이므로, 이 문서에 Version 기반 테스트는 존재하지 않습니다. 수정 경합은 삭제 경합과
동일한 방어를 사용하며 같은 단언으로 검증됩니다([deletion-race-control.md](deletion-race-control.md)).

## 2. 시나리오 매핑

| # | 시나리오 | 기대 결과 | 소유 | AI 테스트 |
|---|---|---|---|---|
| 1 | 처리 중 사용자가 Context 본문 수정 | 구 Context CANCELLED·`is_deleted`, 신 Context가 새 `context_id`로 독립 처리 | AI+ | `test_context_edit_isolates_old_and_new_context` |
| 2 | 수정 후 구 Context 결과 도착 | 저장 거부 | **AI** | `test_cancelled_rejects_late_result` |
| 3 | 수정 후 검색 | 구 Context가 결과에 포함되지 않음 | **AI** | `test_search_excludes_cancelled_and_deleted` |
| 4 | 수정 후 Keyword 조회 | 구 Context Keyword가 COMPLETED였더라도 CANCELLED로 제외 | AI+ | `test_keyword_query_excludes_cancelled_context` |
| 5 | 같은 `context_id`에 다른 `text` 요청 | 계약 위반으로 처리 | **AI** | `test_same_context_id_different_text_is_contract_violation` |
| 6 | 수정 후 구 `context_id` 재처리 요청 | CANCELLED이므로 즉시 종료 | **AI** | `test_process_request_on_cancelled_does_not_start` |
| 7 | Embedding 성공, Keyword 실패 | Embedding 재호출 없이 Keyword만 재개 | **AI** | `test_partial_resume_skips_embedding_call` |
| 8 | PROCESSING 중 서버 종료 | 만료 후 재스캔이 stale 작업을 재개 | AI+ | `test_stale_processing_is_resumable` |
| 9 | 동일 Context 처리 요청 중복 | 결과 중복 저장 없음 | **AI** | `test_concurrent_process_requests_single_effect` |
| 10 | 삭제 중 Embedding 완료 | 저장 거부 | **AI** | `test_cancelled_rejects_embedding_persist` |
| 11 | 삭제 중 Keyword 완료 | 저장 거부 | **AI** | `test_cancelled_rejects_keyword_persist` |
| 12 | Embedding Row가 없는 상태에서 삭제·수정 | CANCELLED가 늦은 INSERT를 차단 | **AI** | `test_cancelled_blocks_late_embedding_insert` |
| 13 | Context와 Preset의 Profile 불일치 | 판정 중단 | **AI** | `test_profile_mismatch_aborts_keyword_stage` |
| 14 | 매칭 Keyword 없음 | 정상 COMPLETED | **AI** | `test_empty_keyword_result_is_completed` |
| 15 | `BLOCKED` Keyword | 조회와 추천 계산에서 제외 | AI+ | `test_blocked_preset_not_in_candidates` |
| 16 | `retry_count = 3` stale 상태 | Finalizer가 미완료 단계만 FAILED | AI+ | `test_fastapi_never_writes_finalizer_failed` |
| 17 | Finalizer 처리 중 Context 삭제 | CANCELLED 우선, FAILED로 덮어쓰지 않음 | AI+ | `test_failed_transition_does_not_overwrite_cancelled` |
| 18 | 재스캔 후보 선택 후 Context 삭제 | 실행 또는 저장 거부 | AI+ | `test_process_request_on_cancelled_does_not_start` |
| 19 | 타인 데이터 검색 시도 | User 범위 필터로 차단 | **AI** | `test_search_scoped_to_user_id` |
| 20 | 한 Record의 여러 Context 일치 | Record를 한 번만 반환 | **AI** | `test_search_dedupes_by_record_with_max_similarity` |
| 21 | AI 미완료 Collection | Keyword 없이 기본 조회와 Feed 가능 | BE | — |

시나리오 6과 18은 AI 관점에서 같은 단언("CANCELLED State에는 아무 작업도 시작되지 않는다")을
공유하므로 하나의 테스트를 공유합니다. 도착 경로(사용자 수정 후 / 재스캔 후보 선택 후)가
다를 뿐 `ai` 스키마에서 보이는 상태는 동일합니다.

## 3. 시나리오별 검증 지점

### 1 — Context 수정

Spring의 수정 트랜잭션 결과를 `ai` 스키마에 재현한 뒤, 구·신 두 Context를 모두 다룹니다.

```text
구 context_id=1: embedding_status=CANCELLED, keyword_status=CANCELLED,
                 context_embedding.is_deleted = true
신 context_id=2: 새 State, 두 status = PENDING, Embedding·Keyword 행 없음
```

단언:

- 구 `context_id`의 저장 시도가 거부됨(→ 시나리오 2와 같은 경로).
- 신 `context_id` 요청이 정상 처리되어 자신의 Embedding·Keyword를 만듦.
- 신 Context 처리가 구 Context의 Embedding을 **재사용하지 않음**.
  Embedding Client 호출 횟수 == 1로 단언합니다([partial-resume.md](partial-resume.md) §2).
- 구 Context의 `retry_count`, `COMPLETED`, `FAILED`가 신 State로 승계되지 않음.

Core 트랜잭션(구 삭제 + 신 INSERT의 원자성) 자체는 back 소유입니다.

### 2 — 수정 후 구 Context 결과 도착

두 status가 CANCELLED인 상태에서 저장 단계를 실행합니다.

단언: `ai.context_embedding`에 벡터가 쓰이지 않음, status가 COMPLETED로 바뀌지 않음,
`is_deleted`가 `false`로 되돌아가지 않음, 예외가 아니라 정상 종료.
근거: [deletion-race-control.md](deletion-race-control.md) §3.2

삭제로 CANCELLED가 된 경우(시나리오 10·11)와 **같은 코드 경로**임을 테스트 이름과 주석에
남깁니다. 수정 전용 방어 코드가 새로 생기면 이 테스트가 무의미해집니다.

### 3 — 수정 후 검색

구 Context 행(`is_deleted = true`, `embedding_status = CANCELLED`)과 신 Context 행
(`is_deleted = false`, `embedding_status = COMPLETED`)을 같은 `record_id`로 넣고 검색합니다.

단언: 결과에 신 Context의 유사도만 반영되고 구 Context 벡터는 후보에 들어가지 않음.
두 조건(`is_deleted`, `embedding_status`)을 각각 하나씩만 걸어 둔 변형 케이스도 두어,
**어느 한쪽 조건을 지워도 반드시 실패**하게 만듭니다. 계약 §9.3의 필터 목록이 그대로
Query에 있는지 확인하는 테스트입니다.

### 4 — 수정 후 Keyword 조회

구 Context에 `keyword_status = COMPLETED`와 `ai.context_keyword` 3행을 만든 뒤
두 status를 CANCELLED로 바꿉니다.

단언: State를 조인한 Keyword 조회 결과가 0행. `ai.context_keyword` 행 자체는
**물리적으로 남아 있어도 됩니다**(계약 §8.4). 행이 지워졌는지가 아니라 조회에서
제외되는지를 단언합니다. FastAPI가 다른 Context의 Keyword를 지우지 않는 것도 함께 단언합니다.

### 5 — 같은 context_id, 다른 text

`context_id`는 그대로 두고 `text`만 바꾼 요청을 보냅니다.

단언:

- 계약 위반 로그(`WARN` 이상)가 남음.
- State가 이미 COMPLETED이면 아무 것도 바뀌지 않음(Embedding·LLM Client 호출 0회).
- FastAPI가 이를 수정으로 해석해 재생성·재판정하거나 State를 되돌리지 **않음**.
- HTTP 응답은 여전히 `202`. 파이프라인이 예외로 죽지 않음.

근거: [deletion-race-control.md](deletion-race-control.md) §2.1, 계약 §13.1

### 6, 18 — CANCELLED State에 도착한 요청

State가 CANCELLED인 상태에서 처리 요청을 보냅니다. 도착 경로는 수정 후 구 `context_id`
재요청(6)과 재스캔 후보 선택 후 삭제(18) 두 가지입니다.

단언: PROCESSING 전환 실패(영향 행 수 0), Embedding·LLM Client 호출 0회,
어떤 테이블에도 쓰기 없음, HTTP 응답은 여전히 `202`, 로그 레벨은 에러가 아님.

### 7 — 부분 재개

`embedding_status = COMPLETED`, `keyword_status = PENDING`, Embedding 행의 Profile 일치.

단언: **Embedding Client 호출 횟수 == 0**, LLM Client 호출 횟수 == 1,
`keyword_status`가 COMPLETED. 근거: [partial-resume.md](partial-resume.md)

### 8 — stale PROCESSING

`embedding_status = PROCESSING`, `updated_at`을 11분 전으로 세팅.

Spring이 재스캔으로 같은 요청을 다시 보내는 흐름을 재현합니다.
Context가 불변이 되면서 State를 `PENDING`으로 되돌리는 경로가 사라졌으므로,
stale 작업의 재개는 **만료된 `PROCESSING`의 재선점**으로만 이루어집니다
([state-machine.md](state-machine.md) §3.1).

단언:

- `updated_at`이 만료된 PROCESSING은 재선점되어 재처리가 정상 완료됨.
- `updated_at`이 방금인 PROCESSING은 재선점되지 **않음**(영향 행 수 0, Client 호출 0회).
  이 짝 테스트가 없으면 만료 조건이 사라져도 회귀가 드러나지 않습니다.
- 재처리 후 `ai.context_embedding` 행 1개, `ai.context_keyword` 행 수가 판정 결과와 일치.

재스캔 주기와 만료 시각 판정 자체는 back 소유입니다.

또한 일시적 오류 경로에서 상태가 PROCESSING으로 남는지(PENDING·FAILED로 바뀌지 않는지)를
단언합니다. 근거: [failure-recovery.md](failure-recovery.md) §2.1

### 9 — 중복 요청

같은 `contextId` 요청 2개를 동시에 실행합니다.

단언: Embedding Client 호출 1회, `ai.context_embedding` 행 1개,
`ai.context_keyword` 행 수가 판정 결과 개수와 일치(2배가 아님).
조건부 UPDATE가 유일한 방어이므로 실제 동시 실행으로 검증합니다.

### 10, 11 — 삭제 중 완료

모델 호출 시점과 저장 시점 사이에 State를 CANCELLED로 바꾸는 훅을 Client Fake에 넣습니다.

단언: 결과 미저장, status가 CANCELLED로 유지(FAILED로 덮이지 않음),
`is_deleted`가 `false`로 되돌아가지 않음.

`is_deleted` 단언은 UPSERT의 SET 절 회귀를 잡는 테스트입니다. `is_deleted = true`인 행에
UPSERT를 시도해 값이 `true`로 유지되는지 별도 단위 테스트로도 고정합니다.

### 12 — Embedding Row가 없는 상태에서의 삭제·수정

`ai.context_embedding`에 행이 **없는** 상태에서 State만 CANCELLED로 바꾼 뒤,
Embedding API 응답이 뒤늦게 도착하는 흐름을 재현합니다.

단언: `ai.context_embedding`에 행이 생기지 않음. 이 상황에서는 걸어 둘 `is_deleted`가
없으므로 **CANCELLED만이 유일한 방어선**이며, 저장 직전 status 검사를 "행이 이미 있는지"나
`is_deleted` 조회로 대체하면 반드시 실패해야 합니다.
근거: [deletion-race-control.md](deletion-race-control.md) §5, 계약 §11.2

### 13 — Profile 불일치

Preset은 Profile A, Context Embedding은 Profile B로 준비합니다.

단언: LLM Client 호출 0회, `ai.context_keyword` 행 0개,
`keyword_status`가 COMPLETED가 **아님**(판정 불가와 결과 0개를 구분).

### 14 — 매칭 없음

LLM Fake가 빈 `selected`를 반환하도록 합니다.

단언: `keyword_status = COMPLETED`, `ai.context_keyword` 0행,
`ai.context_keyword_analysis` 행 존재.

### 15 — BLOCKED

`visibility = 'BLOCKED'`인 Preset을 넣고 후보 검색을 실행합니다.

단언: 후보 목록과 LLM 입력에 해당 Preset이 없음. 최종 응답에서의 제외는 back 소유입니다.

### 16 — retry_count 소진

`retry_count = 3`, 대상 단계가 stale한 상태를 만듭니다.

Finalizer 자체는 Spring 소유이므로 AI 쪽에서 단언하는 것은 **경계 준수**입니다.

- FastAPI가 `retry_count`를 읽지도 쓰지도 않음.
- FastAPI가 재시도 소진을 이유로 `FAILED`를 쓰지 않음(계약 §6.4).
- Finalizer가 남긴 `FAILED` 상태에 처리 요청이 도착해도 재개하지 않음
  (`FAILED → PROCESSING` 전이 없음, 두 Client 호출 0회).

### 17 — Finalizer 처리 중 삭제

status가 CANCELLED인 상태에서 FastAPI의 영구 오류 FAILED 전이를 실행합니다.

단언: status가 CANCELLED로 유지됨. FastAPI의 FAILED UPDATE는 WHERE 절의 `PROCESSING`
조건 때문에 영향 행 수 0으로 끝나야 합니다. Spring Finalizer 쪽 동일 규칙은 back 소유입니다.
근거: [state-machine.md](state-machine.md) §3.4, 계약 §11.1

### 19 — 타인 데이터

user A와 user B의 Embedding을 넣고 user A로 검색합니다.

단언: 결과에 B의 record가 없음. `limit`을 전체 행 수보다 크게 잡아 "우연히 안 나온 것"이
아님을 보장합니다.

### 20 — Record 중복 제거

한 record에 속한 Context 3개를 서로 다른 유사도로 준비합니다.

단언: 결과에 해당 `recordId`가 정확히 1회, `similarity`가 세 값 중 최댓값과 일치.

## 4. Fixture 전략

### 4.1 DB

- **실제 PostgreSQL + pgvector**를 사용합니다. SQLite나 인메모리 DB로 대체하지 않습니다.
  이 문서의 테스트 대부분이 검증하는 것은 조건부 UPDATE의 영향 행 수, `FOR UPDATE` 동작,
  `<=>` 연산자, `ON CONFLICT` SET 절이며, 전부 방언 의존적입니다.
- Testcontainers로 `pgvector` 이미지를 띄우고 세션 스코프로 재사용합니다.
- **ai 레포는 Migration을 실행하지 않습니다.** 테스트용 스키마는 back의 migration에서
  파생된 스냅샷 SQL을 `tests/schema/` 에 두고 적용합니다. 이 스냅샷은 테스트 전용이며
  운영 스키마의 원본이 아닙니다. back의 migration이 바뀌면 스냅샷을 갱신하고,
  갱신 누락은 테스트 실패로 드러납니다.
- 테스트 간 격리는 트랜잭션 롤백이 아니라 **테이블 TRUNCATE**로 합니다.
  동시성 테스트가 여러 커넥션을 사용하므로 롤백 격리를 쓸 수 없습니다.

### 4.2 외부 Client

`embedding_client`와 `llm_client`는 프로토콜로 정의하고 테스트에서 Fake로 교체합니다.
HTTP 레벨 목이 아니라 인터페이스 레벨 Fake를 씁니다.

| Fake | 기능 |
|---|---|
| `FakeEmbeddingClient` | 텍스트 → 결정론적 벡터, **호출 횟수 기록**, 지연·예외·차원 오류 주입 |
| `FakeLLMClient` | 고정 `selected` 반환, 후보 밖 ID 반환, 스키마 위반, 호출 횟수 기록 |

- 벡터는 텍스트 해시 기반으로 생성해 재현 가능하게 합니다. 무작위 벡터를 쓰면
  유사도 순서 단언이 흔들립니다. 본문이 다르면 벡터도 달라지므로, 시나리오 1에서
  구·신 Context의 벡터가 실제로 다른 값인지 확인할 수 있습니다.
- 유사도 순서를 검증하는 테스트(20번)는 벡터를 직접 지정해 기대 순서를 고정합니다.
- **호출 횟수 기록은 선택 기능이 아닙니다.** 시나리오 1·5·6·7·9·13·16·18의 핵심 단언이
  "호출하지 않았다" 또는 "정확히 한 번 호출했다"이기 때문입니다.
- 실제 외부 API를 호출하는 테스트는 두지 않습니다. CI에서 비용과 비결정성이 발생합니다.

### 4.3 데이터 빌더

State·Embedding·Preset을 만드는 빌더를 두고, 테스트는 관심 있는 값만 지정합니다.

```python
state = make_state(context_id=1,
                   embedding_status="COMPLETED", keyword_status="PENDING")
emb = make_embedding(context_id=1, user_id=7, record_id=3, is_deleted=False)
preset = make_preset(code="WITH_FRIEND", visibility="PUBLIC")
```

- `embedding_profile`, `is_deleted`, 두 status는 **항상 명시적으로** 지정 가능해야 합니다.
  이 값들의 조합이 곧 테스트 대상입니다.
- 빌더에 Context 본문 버전에 해당하는 인자를 두지 않습니다. 그런 컬럼이 존재하지 않으며,
  테스트 헬퍼에 남겨 두면 제거된 개념이 코드로 되살아납니다.
- Context 수정 시나리오는 `version=2`가 아니라 **`context_id`가 다른 두 State**로
  표현합니다. 이것이 계약 §4.2의 모델을 테스트에 그대로 반영하는 방식입니다.
- Embedding Profile은 설정 객체에서 읽습니다. 테스트에 Profile 문자열 리터럴을 두지 않습니다
  ([model-profile.md](model-profile.md) §2.1).

### 4.4 동시성

시나리오 9와 10·11·12는 실제 동시 실행으로 검증합니다.

- 시나리오 9: 서로 다른 커넥션에서 두 파이프라인을 `asyncio.gather`로 실행.
- 시나리오 10·11·12: Fake Client의 호출 훅에서 다른 커넥션으로 State를 CANCELLED로 변경.
  모델 호출과 저장 사이의 창을 결정론적으로 재현하는 방식입니다.
  시나리오 12는 이때 `ai.context_embedding` 행을 만들지 **않은 채** CANCELLED만 겁니다.
- 시나리오 1·2의 수정 경합도 같은 훅을 재사용합니다. 수정 경합 전용 도구를 만들지 않는 것이
  "수정 경합은 삭제 경합으로 흡수된다"는 설계를 테스트 코드에서도 유지하는 방법입니다.
- `sleep` 기반 타이밍 의존 테스트를 만들지 않습니다. 훅으로 순서를 고정합니다.

## 5. 테스트 계층

| 계층 | 대상 | DB |
|---|---|---|
| 단위 | 오류 분류, 후보 TOP-K 계산, LLM 결과 매핑·폐기, Profile 검증 | 없음 |
| 저장소 | 조건부 UPDATE 영향 행 수, UPSERT SET 절, delete-insert, 검색 Query | 실제 |
| 파이프라인 | §3의 시나리오 전체 | 실제 |
| API | 요청 스키마 검증, `202` 반환, 검색 응답 형식, Profile 불일치 `422` | 실제 |

파이프라인 계층이 이 레포 테스트의 중심입니다. 계약 §16의 시나리오는 모두 여러 계층에 걸친
상태 전이 문제이므로, 단위 테스트만으로는 검증되지 않습니다.

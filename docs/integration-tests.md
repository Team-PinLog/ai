> 현재 코드가 없는 구현 예정 명세입니다.
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# AI 파트 통합 테스트

근거 계약: `static/05_AI_설계.md` §16 필수 검증 시나리오

## 1. 범위

계약 §16의 15개 시나리오 중 **AI 파트가 소유하는 것**을 이 레포에서 검증합니다.
소유하지 않는 시나리오도 표에 남겨 두어 어디서 검증되는지 추적할 수 있게 합니다.

- `AI` — ai 레포에서 검증. 이 문서가 테스트 정의를 소유합니다.
- `AI+` — 양쪽에서 검증. ai 레포는 `ai` 스키마 관점만 검증합니다.
- `BE` — back 레포 소유. ai 레포는 테스트를 두지 않습니다.

## 2. 시나리오 매핑

| # | 시나리오 | 기대 결과 | 소유 | AI 테스트 |
|---|---|---|---|---|
| 1 | v1 처리 중 Context가 v2로 수정됨 | v1 결과 폐기, v2 재처리 | **AI** | `test_stale_version_result_discarded` |
| 2 | 수정 직후 검색 | 구 Embedding이 결과에 포함되지 않음 | **AI** | `test_search_excludes_version_mismatch` |
| 3 | 수정 직후 Keyword 조회 | 구 Keyword가 공개되지 않음 | AI+ | `test_keyword_delete_insert_removes_stale_rows` |
| 4 | Embedding 성공, Keyword 실패 | Embedding 재호출 없이 Keyword만 재개 | **AI** | `test_partial_resume_skips_embedding_call` |
| 5 | PROCESSING 중 서버 종료 | 만료 후 재스캔이 stale 작업을 재개 | AI+ | `test_stale_processing_is_resumable` |
| 6 | 동일 Context 처리 요청 중복 | 결과 중복 저장 없음 | **AI** | `test_concurrent_process_requests_single_effect` |
| 7 | 삭제 중 Embedding 완료 | 저장 거부 | **AI** | `test_cancelled_rejects_embedding_persist` |
| 8 | 삭제 중 Keyword 완료 | 저장 거부 | **AI** | `test_cancelled_rejects_keyword_persist` |
| 9 | Context와 Preset의 Profile 불일치 | 판정 중단 | **AI** | `test_profile_mismatch_aborts_keyword_stage` |
| 10 | 매칭 Keyword 없음 | 정상 COMPLETED | **AI** | `test_empty_keyword_result_is_completed` |
| 11 | `BLOCKED` Keyword | 조회와 추천 계산에서 제외 | AI+ | `test_blocked_preset_not_in_candidates` |
| 12 | 재스캔 후보 선택 후 Context 삭제 | 실행 또는 저장 거부 | AI+ | `test_process_request_on_cancelled_does_not_start` |
| 13 | 타인 데이터 검색 시도 | User 범위 필터로 차단 | **AI** | `test_search_scoped_to_user_id` |
| 14 | 한 Record의 여러 Context 일치 | Record를 한 번만 반환 | **AI** | `test_search_dedupes_by_record_with_max_similarity` |
| 15 | AI 미완료 Collection | Keyword 없이 기본 조회와 Feed 가능 | BE | — |

## 3. 시나리오별 검증 지점

### 1 — 구버전 결과 폐기

State를 v2로 올려 둔 상태에서 v1 요청의 저장 단계를 실행합니다.

단언: `ai.context_embedding`에 v1 벡터가 없음, `embedding_status`가 COMPLETED로 바뀌지 않음,
예외가 아니라 정상 종료. 근거: [version-race-control.md](version-race-control.md)

### 2 — 수정 직후 검색

`context_embedding.context_version = 1`, `context_ai_state.context_version = 2`인 행을 만들고 검색.

단언: 결과에 해당 record가 없음. Query의 `s.context_version = e.context_version` 조건이 실제로
작동하는지 확인하는 테스트이므로, 조건을 제거하면 반드시 실패해야 합니다.

### 3 — 구 Keyword 제거

v1 판정으로 3개를 저장한 뒤 v2 판정 결과 0개로 저장합니다.

단언: `ai.context_keyword`의 해당 `context_id` 행 수가 0. UPSERT 구현이면 실패하는 테스트입니다.
근거: [keyword-preset.md](keyword-preset.md) §5

### 4 — 부분 재개

`embedding_status = COMPLETED`, `keyword_status = PENDING`, Embedding 행의 Version·Profile 일치.

단언: **Embedding Client 호출 횟수 == 0**, LLM Client 호출 횟수 == 1,
`keyword_status`가 COMPLETED. 근거: [partial-resume.md](partial-resume.md)

### 5 — stale PROCESSING

`embedding_status = PROCESSING`, `updated_at`을 11분 전으로 세팅.

AI 쪽에서 검증하는 것은 만료 판정이 아니라 **회수 이후의 동작**입니다.
Spring이 이 Context를 PENDING으로 되돌린 뒤 재요청하는 흐름을 재현해, 재처리가 정상 완료되고
결과가 중복 저장되지 않는지 단언합니다. 만료 시각 판정 자체는 back 소유입니다.

또한 일시적 오류 경로에서 상태가 PROCESSING으로 남는지(PENDING·FAILED로 바뀌지 않는지)를
단언합니다. 근거: [failure-recovery.md](failure-recovery.md) §2.1

### 6 — 중복 요청

같은 `contextId` / `contextVersion` 요청 2개를 동시에 실행합니다.

단언: Embedding Client 호출 1회, `ai.context_embedding` 행 1개,
`ai.context_keyword` 행 수가 판정 결과 개수와 일치(2배가 아님).
조건부 UPDATE가 유일한 방어이므로 실제 동시 실행으로 검증합니다.

### 7, 8 — 삭제 중 완료

모델 호출 시점과 저장 시점 사이에 State를 CANCELLED로 바꾸는 훅을 Client Fake에 넣습니다.

단언: 결과 미저장, status가 CANCELLED로 유지(FAILED로 덮이지 않음),
`is_deleted`가 `false`로 되돌아가지 않음.

`is_deleted` 단언은 UPSERT의 SET 절 회귀를 잡는 테스트입니다. `is_deleted = true`인 행에
UPSERT를 시도해 값이 `true`로 유지되는지 별도 단위 테스트로도 고정합니다.

### 9 — Profile 불일치

Preset은 Profile A, Context Embedding은 Profile B로 준비합니다.

단언: LLM Client 호출 0회, `ai.context_keyword` 행 0개,
`keyword_status`가 COMPLETED가 **아님**(판정 불가와 결과 0개를 구분).

### 10 — 매칭 없음

LLM Fake가 빈 `selected`를 반환하도록 합니다.

단언: `keyword_status = COMPLETED`, `ai.context_keyword` 0행,
`ai.context_keyword_analysis` 행 존재.

### 11 — BLOCKED

`visibility = 'BLOCKED'`인 Preset을 넣고 후보 검색을 실행합니다.

단언: 후보 목록과 LLM 입력에 해당 Preset이 없음. 최종 응답에서의 제외는 back 소유입니다.

### 12 — 재스캔 후보 선택 후 삭제

State가 CANCELLED인 상태에서 처리 요청을 보냅니다.

단언: PROCESSING 전환 실패, Embedding·LLM Client 호출 0회, 어떤 테이블에도 쓰기 없음,
HTTP 응답은 여전히 `202`.

### 13 — 타인 데이터

user A와 user B의 Embedding을 넣고 user A로 검색합니다.

단언: 결과에 B의 record가 없음. `limit`을 전체 행 수보다 크게 잡아 "우연히 안 나온 것"이
아님을 보장합니다.

### 14 — Record 중복 제거

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
  유사도 순서 단언이 흔들립니다.
- 유사도 순서를 검증하는 테스트(14번)는 벡터를 직접 지정해 기대 순서를 고정합니다.
- **호출 횟수 기록은 선택 기능이 아닙니다.** 시나리오 4·6·9·12의 핵심 단언이
  "호출하지 않았다"이기 때문입니다.
- 실제 외부 API를 호출하는 테스트는 두지 않습니다. CI에서 비용과 비결정성이 발생합니다.

### 4.3 데이터 빌더

State·Embedding·Preset을 만드는 빌더를 두고, 테스트는 관심 있는 값만 지정합니다.

```python
state = make_state(context_id=1, version=2,
                   embedding_status="COMPLETED", keyword_status="PENDING")
emb = make_embedding(context_id=1, version=2, user_id=7, record_id=3)
preset = make_preset(code="WITH_FRIEND", visibility="PUBLIC")
```

- `context_version`, `embedding_profile`, `is_deleted`, 두 status는 **항상 명시적으로**
  지정 가능해야 합니다. 이 값들의 조합이 곧 테스트 대상입니다.
- Embedding Profile은 설정 객체에서 읽습니다. 테스트에 Profile 문자열 리터럴을 두지 않습니다
  ([model-profile.md](model-profile.md) §2.1).

### 4.4 동시성

시나리오 6과 7·8은 실제 동시 실행으로 검증합니다.

- 시나리오 6: 서로 다른 커넥션에서 두 파이프라인을 `asyncio.gather`로 실행.
- 시나리오 7·8: Fake Client의 호출 훅에서 다른 커넥션으로 State를 CANCELLED로 변경.
  모델 호출과 저장 사이의 창을 결정론적으로 재현하는 방식입니다.
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

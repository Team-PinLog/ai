# FastAPI 구현 — scaffold 와 /context/process·/search 를 계약 명세대로 구현했다

- **상태**: 완료
- **날짜**: 2026-07-23
- **관련 PR**: [ai#5](https://github.com/Team-PinLog/ai/pull/5)(scaffold + `/search` + Preset 부트스트랩), [ai#6](https://github.com/Team-PinLog/ai/pull/6)(`/context/process` 파이프라인 + 상태머신)
- **근거 계약**: `static/05_AI_설계.md`, [spec/](../spec/) 전 문서
- **판정 모델**: `gemini-2.5-flash` — 테스트 C-2 에서 확정했다([P26](../proposals/P26-keyword-preset-judgment.md))

## 무엇을 만들었나

`spec/` 의 계약 명세를 실제 FastAPI 서버로 구현했다. 내부 엔드포인트 두 개(`/internal/v1/context/process`, `/internal/v1/search`)와 Preset 부트스트랩 CLI 를 만들었다. 앱 코드는 약 1,470줄이다(`app/`).

DB 접근은 asyncpg 와 원시 SQL 로 구현했고 ORM 을 도입하지 않았다. 이유는 다음과 같다. 테이블이 5개뿐이고, guarded UPDATE·`FOR UPDATE`·UPSERT·delete-insert·pgvector 연산이 spec 에 이미 SQL 문으로 명시되어 있다. 이 조건에서는 원시 SQL 을 쓰는 쪽이 구현 코드와 계약 문서의 SQL 을 1:1 로 대응시킨다.

## 구현 파일 (architecture.md §3 모듈 구조)

| 계층 | 파일 | 역할 |
|---|---|---|
| core | `config.py` | 단일 Embedding Profile 주입. model/dim/distance 가 불일치하면 기동에 실패한다 |
| core | `db.py` | asyncpg 풀. `search_path=ai, public` 고정(public 은 vector 확장이 있는 스키마이고, core 는 경로 밖에 유지한다. T21 참조). pgvector 타입 등록 |
| core | `errors.py` | 영구/일시 오류와 저장 폐기의 분류 |
| core | `security.py` | 내부 공유 시크릿 미들웨어(`/internal/*` 경로에 적용) |
| client | `embedding_client.py` | GMS 의 OpenAI 호환 `/embeddings` 호출(하네스 `embed.py` 를 포팅했다) |
| client | `llm_client.py` | GMS 의 Gemini `generateContent` 호출. responseSchema 와 thinkingBudget=0 을 사용한다 |
| cache | `preset_cache.py` | 기동 시 `is_active` 이고 Profile 이 일치하는 Preset 을 적재한다. BLOCKED 는 제외하고, 적재 결과가 0건이면 기동에 실패한다 |
| repository | `ai_state_repo.py` | 조건부 상태 전이(try_start/complete/fail). 컬럼 조립에는 Stage 열거형만 쓴다 |
| repository | `context_embedding_repo.py` | 검색 Query 와 UPSERT(`is_deleted` 는 갱신 대상에서 제외)와 fallback 조회 |
| repository | `context_keyword_repo.py` | 키워드 delete-insert 와 analysis UPSERT |
| repository | `keyword_preset_repo.py` | Preset 적재 조회 |
| service | `search_service.py` | 질의 1회 임베딩 → 정확 cosine 계산 → Record 단위 집계 |
| service | `embedding_service.py` | 임베딩 재사용 판정 + 생성 + 저장 트랜잭션 |
| service | `keyword_service.py` | 후보 TOP-K 선정 + LLM 판정 + delete-insert 저장 |
| service | `context_processing.py` | 파이프라인 오케스트레이션 |
| api | `internal/v1/{search,context}.py` | 라우터. context 는 202 응답 후 BackgroundTask 로 처리한다 |
| bootstrap | `load_presets.py` | `data/keyword_preset.yaml` → 임베딩 생성 → `ai.keyword_preset` UPSERT |

## 계약 대비 커버 범위

| spec | 구현 반영 |
|---|---|
| [architecture](../spec/architecture.md) | 계층 구조·세션 경계·`ai` 스키마 한정·`core.*` 미접근 |
| [context-processing](../spec/context-processing.md) | 사전 검사 → 재개 → Embedding → Keyword 순서, 저장 불변식 |
| [state-machine](../spec/state-machine.md) | guarded 전이(PENDING/만료 PROCESSING→PROCESSING, →COMPLETED/FAILED). CANCELLED·retry_count·is_deleted 는 기록하지 않는다 |
| [partial-resume](../spec/partial-resume.md) | 재사용 2조건. 벡터를 재사용하면 임베딩 API 를 다시 호출하지 않는다(재호출 0회) |
| [personal-search](../spec/personal-search.md) | 정확 cosine 계산과 Record `MAX` 집계, Profile 불일치 시 422 |
| [keyword-preset](../spec/keyword-preset.md) | 캐시 기반 TOP-K(floor 0.30), 후보 밖 판정 폐기, delete-insert, unmatchedConcepts |
| [model-profile](../spec/model-profile.md) | Profile 단일 주입, 차원 검증, 불일치 시 동작 |
| [deletion-race-control](../spec/deletion-race-control.md) | 저장 직전 `FOR UPDATE` 재검사로 늦은 INSERT 를 폐기한다 |
| [failure-recovery](../spec/failure-recovery.md) | 영구 오류는 FAILED 로, 일시 오류는 상태를 유지해 재스캔이 회수한다 |

**미구현(후속)**: [integration-tests](../spec/integration-tests.md)의 자동화(Testcontainers)와 Dockerfile 은 E3 단계에서 한다. 현재는 로컬 수동 검증까지다.

## 검증 방법

로컬에서 `pgvector/pgvector:pg16` 컨테이너를 띄우고 back 레포의 Flyway 마이그레이션(V1/V100/V101)으로 `ai.*` 테이블을 생성했다. 부트스트랩으로 Preset 27건을 적재한 뒤, 실제 GMS(임베딩·Gemini)를 호출해 end-to-end 로 확인했다.

**`/search`**

- 질의 "친구들이랑 모임"에는 친구 관련 record 가 최상위(유사도 0.56)로 반환됐고, "가족과 저녁"에는 가족 record(0.72)가 반환됐다. 의미 기반 매칭이 의도대로 동작한다.
- `userId` 범위 필터가 다른 유저의 record 를 결과에서 차단하는 것을 확인했다.
- Embedding Profile 이 불일치하면 422 를, 내부 시크릿이 없으면 401 을 반환했다. `/health` 는 ok 를 반환했다.

**`/context/process`** — PENDING 상태 행을 먼저 삽입한 뒤 호출했다.

| 시나리오 | 결과 |
|---|---|
| 정상 | embedding·keyword·analysis 가 저장됐다. "여자친구/기념일" 본문에 WITH_PARTNER/MEAL/DATE_COURSE 키워드가 붙었다 |
| 후보/판정 0건 | keyword 단계가 COMPLETED 로 끝나고 키워드는 0건으로 저장됐다. "주차/화장실" 본문은 부대시설 제외 규칙에 따라 판정이 기각됐다 |
| CANCELLED | 처리를 거부했고 embedding 을 저장하지 않았다 |
| 부분 재개 | keyword 단계만 PENDING 인 상태에서 판정만 다시 실행했다. 임베딩 API 재호출은 0회였다(로그로 확인) |

## 구현 중 해결한 이슈

- `.env` 파일이 UTF-8 BOM 으로 저장되어 첫 번째 키의 파싱이 실패했다. PowerShell `Set-Content` 의 기본 인코딩이 BOM 을 붙이는 것이 원인이었다. BOM 없이 다시 저장해 해결했다.
- pgvector 가 embedding 컬럼을 `Vector` 객체로 반환했다. `to_numpy()` 로 변환해 처리했다.
- asyncpg 가 `now() - $2` 식의 파라미터 타입을 추론하지 못했다(`timestamptz < interval` 비교). `$2::interval` 명시 캐스트로 해결했다.

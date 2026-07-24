# FastAPI 구현 — scaffold + /context/process + /search

- **상태**: 완료
- **날짜**: 2026-07-23
- **관련 PR**: [ai#5](https://github.com/Team-PinLog/ai/pull/5)(scaffold + `/search` + Preset 부트스트랩), [ai#6](https://github.com/Team-PinLog/ai/pull/6)(`/context/process` 파이프라인 + 상태머신)
- **근거 계약**: `static/05_AI_설계.md`, [spec/](../spec/) 전 문서
- **판정 모델**: `gemini-2.5-flash`(테스트 C-2 확정, [P26](../proposals/P26-keyword-preset-judgment.md))

## 무엇을 만들었나

`spec/`의 계약 명세를 실제 FastAPI 서버로 구현했다. 두 내부 엔드포인트(`/internal/v1/context/process`, `/internal/v1/search`)와 Preset 부트스트랩 CLI. 앱 코드 약 1,470줄(`app/`).

DB 접근은 **asyncpg + 원시 SQL**(계약 SQL 그대로). ORM을 두지 않았다 — 테이블이 5개이고 guarded UPDATE·`FOR UPDATE`·UPSERT·delete-insert·pgvector 연산이 spec에 이미 SQL로 명시돼 있어, 원시 SQL이 계약과 1:1로 대응한다.

## 구현 파일 (architecture.md §2 계층)

| 계층 | 파일 | 역할 |
|---|---|---|
| core | `config.py` | 단일 Embedding Profile 주입, model/dim/distance 불일치 시 기동 실패 |
| core | `db.py` | asyncpg 풀, `search_path=ai` 고정, pgvector 등록 |
| core | `errors.py` | 영구/일시 오류 + 저장 폐기 분류 |
| core | `security.py` | 내부 공유 시크릿 미들웨어(`/internal/*`) |
| client | `embedding_client.py` | GMS OpenAI 호환 `/embeddings`(하네스 `embed.py` 포팅) |
| client | `llm_client.py` | GMS Gemini `generateContent` + responseSchema + thinkingBudget=0 |
| cache | `preset_cache.py` | 기동 시 `is_active` + Profile 일치 Preset 적재, BLOCKED 제외, 0건이면 기동 실패 |
| repository | `ai_state_repo.py` | 조건부 전이(try_start/complete/fail), Stage 열거형만 컬럼 조립 |
| repository | `context_embedding_repo.py` | 검색 Query + UPSERT(`is_deleted` 제외) + fallback 조회 |
| repository | `context_keyword_repo.py` | delete-insert + analysis UPSERT |
| repository | `keyword_preset_repo.py` | Preset 적재 조회 |
| service | `search_service.py` | 질의 1회 임베딩 → 정확 cosine → Record 집계 |
| service | `embedding_service.py` | 재사용 판정 + 생성 + 저장 TX |
| service | `keyword_service.py` | 후보 TOP-K + 판정 + delete-insert 저장 |
| service | `context_processing.py` | 파이프라인 오케스트레이션 |
| api | `internal/v1/{search,context}.py` | 라우터(context는 202 + BackgroundTask) |
| bootstrap | `load_presets.py` | `data/keyword_preset.yaml` → 임베딩 → `ai.keyword_preset` UPSERT |

## 계약 대비 커버 범위

| spec | 구현 반영 |
|---|---|
| [architecture](../spec/architecture.md) | 계층·세션 경계·`ai` 스키마 한정·`core.*` 미접근 |
| [context-processing](../spec/context-processing.md) | 사전 검사 → 재개 → Embedding → Keyword, 저장 불변식 |
| [state-machine](../spec/state-machine.md) | guarded 전이(PENDING/만료 PROCESSING→PROCESSING, →COMPLETED/FAILED), CANCELLED·retry_count·is_deleted 미기록 |
| [partial-resume](../spec/partial-resume.md) | 재사용 2조건, 벡터 재사용 시 임베딩 재호출 0 |
| [personal-search](../spec/personal-search.md) | 정확 cosine + Record `MAX` 집계, Profile 불일치 422 |
| [keyword-preset](../spec/keyword-preset.md) | 캐시 TOP-K(floor 0.30), 후보 밖 폐기, delete-insert, unmatchedConcepts |
| [model-profile](../spec/model-profile.md) | Profile 단일 주입, 차원 검증, 불일치 동작 |
| [deletion-race-control](../spec/deletion-race-control.md) | 저장 직전 `FOR UPDATE` 재검사(늦은 INSERT 폐기) |
| [failure-recovery](../spec/failure-recovery.md) | 영구=FAILED, 일시=상태 유지(재스캔 회수) |

**미구현(후속)**: [integration-tests](../spec/integration-tests.md) 자동화(Testcontainers) + Dockerfile은 E3에서. 현재는 로컬 수동 검증.

## 검증 방법

로컬 `pgvector/pgvector:pg16` + back Flyway(V1/V100/V101)로 `ai.*` 생성 + 부트스트랩 27 Preset 적재 후, 실제 GMS(임베딩·Gemini) 호출로 end-to-end 확인.

**`/search`**
- "친구들이랑 모임" → 친구 record 최상위(0.56), "가족과 저녁" → 가족 record(0.72) — 의미 매칭 정확
- `userId` 범위 필터로 타 유저 record 차단
- Profile 불일치 → **422**, 내부 시크릿 없음 → **401**, `/health` → ok

**`/context/process`** (PENDING state 선삽입 후 호출)
| 시나리오 | 결과 |
|---|---|
| 정상 | embedding+keyword+analysis 저장, "여자친구/기념일" → WITH_PARTNER/MEAL/DATE_COURSE |
| 후보/판정 0 | keyword COMPLETED + 0건("주차/화장실" 부대시설 판정 기각) |
| CANCELLED | 처리 거부, embedding 미저장 |
| 부분 재개 | keyword만 PENDING → **임베딩 재호출 0회**(로그 확인), 판정만 재실행 |

## 구현 중 해결한 이슈

- `.env` UTF-8 BOM → 첫 키 파싱 실패(PowerShell `Set-Content` 기본 BOM). BOM 없이 재작성.
- pgvector가 embedding을 `Vector` 객체로 반환 → `to_numpy()` 변환.
- asyncpg `now() - $2` 파라미터 타입 추론 실패(`timestamptz < interval`) → `$2::interval` 캐스트.

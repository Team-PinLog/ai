# S1 구현 판단 맥락 복원 — FastAPI AI 서버

- **상태**: 완료 (복원)
- **날짜**: 2026-07-28
- **유형**: 구현 (판단 맥락 복원)
- **관련 PR/커밋**: ai#5·#6(E1·E2) · #3(eval) · #7·#8(문서) · #10·#11(contextId) · #14·#16·#17·#18(E3) · #20(주석 정정) · #24(PR2 반영+dead config)
- **근거 원본**: `pinlog/.claude/state/S1-RECOVERY-PACKET.md`(종료 S1 세션 transcript 1,245 이벤트 정독) · `S1-DOC-GAP-TABLE.md`(origin/main 코드·문서 전수 대조)

> 종료된 **S1(AI 파트 작업)** 세션이 남긴 구현 판단을 영구 보존한다. 코드·spec으로 드러나지 않는 "왜"가 대상이다.
> **출처 표기**: `기록복원`(transcript 확인) · `직접확인`(origin/main 코드) · `문서` · `추정` · `미복원`.
> 근거가 없으면 문장을 채우지 않고 **`미복원`으로 확정**한다 — 원 구현자의 판단 이유를 창작하지 않는다.

## 1. 구현 범위·최종 구조

`POST /internal/v1/search`(동기) · `POST /internal/v1/context/process`(202 접수 + 백그라운드) · `/health`. 전부 `/internal/v1/*` 내부 전용, 공유 시크릿 미들웨어. 기동 시 `bootstrap/load_presets`로 keyword_preset 임베딩 27건 적재. `tools/keyword_eval` 오프라인 평가 하네스가 **선행 존재**해 판정 모델을 확정한 뒤 앱으로 포팅. (`기록복원`)

계층 단방향 `api → service → {repository, cache, client}`. **repository는 rowcount만 돌려주고 중단 판단은 service가 한다.** client는 DB를, repository는 외부 API를 모른다. (`기록복원`; 계약 architecture §3 준수)

## 2. 비자명한 설계 선택 19건 (전부 `기록복원`)

| # | 선택 | 근거 |
|---|---|---|
| 1 | asyncpg + 원시 SQL(ORM 미사용) | 계약이 SQL 본문을 정본으로 명시 → 그대로 옮기는 것이 준수에 유리. architecture는 "async 세션"만 규정한 열린 결정이라 `AskUserQuestion`으로 사용자 확정 |
| 2 | 판정 LLM = `gemini-2.5-flash` + `responseSchema` + `thinkingBudget=0` | eval C-2 실측(최속 1.12s·최소 토큰 25,314·스키마 위반 0) |
| 3 | function-calling 대신 native `responseSchema` | gemini-2.5-flash가 function-calling에서 응답 malform |
| 4 | `keywordId` JSON-schema enum 미사용 → 후처리 필터로 멤버십 강제 | |
| 5 | `/search` 집계 `GROUP BY+MAX` → `DISTINCT ON (record_id)` | Record별 최고 유사도 **행 자체**를 골라야 그 행의 context_id를 대표값으로 반환 가능. 계약·시그니처 무변경 |
| 6 | `SET search_path = ai, public` | `vector` 타입이 public에 있어 `ai`만 고정하면 VECTOR 해석·`register_vector` 실패. public을 넣어도 core는 경로 밖 (T21) |
| 7 | pgvector 태그 고정, 롤링 `pg16` 금지 | 롤링은 재빌드 시 마이너가 조용히 바뀌어 CI 비결정적. "당장 깨질 위험은 낮다"고 자평(ANN 없음)하되 통일 비용 0이라 채택. **정본은 운영 이미지·ai 테스트가 따라감** |
| 8 | Python 3.12 + 상한 `<3.13` | 로컬/CI/미래의 **환경 3분할이 최악**. 상한 없으면 합류자가 3.14로 또 갈라짐. 3.12 근거 = **GraphRAG 전제**(torch/transformers/igraph 최신 wheel 수개월 지연). 고정 5곳 |
| 9 | 신규 `ci.yml` 대신 기존 `ai-ci.yml` 수정 | 중복 방지. 이미 3.12라 lock 전환·dev deps·Jira 스텝만 추가 |
| 10 | Jira 키 검증은 PR 제목만 | squash라 PR 제목이 최종 커밋 메시지 |
| 11 | 브랜치 보호 = required check만, 리뷰 0, `enforce_admins:false` | 1인 레포 self-merge + 긴급 여지. **적용을 "CI 그린 후"로 미룬 판단이 옳았음 확인** — 먼저 켰으면 CI 핫픽스 PR 자체가 막힘 |
| 12 | 동시성 테스트 `sleep` 금지, `on_call` 훅 | Fake `on_call`에서 `raw_connect`로 다른 커넥션 열어 CANCELLED 주입 + `asyncio.gather` |
| 13 | Fake는 인터페이스 레벨 + 호출 횟수 기록 | 단언 핵심이 `call_count == 0/1`. 실 GMS 호출 금지 |
| 14 | Profile 문자열 리터럴 금지 — `settings` fixture 경유 | |
| 15 | builders에 "본문 버전" 인자 없음 | Context 불변 → 수정 시나리오는 context_id 다른 두 State로 |
| 16 | `settings` fixture에서 `get_settings()` 캐시 재설정 | `main.py` 모듈 레벨 `app=create_app()`가 import 시점 `.env` 캐시 (→ T26) |
| 17 | conftest placeholder env 선주입 | CI에 `.env` 없어 import 시 죽음 |
| 18 | dead config `preset_cache_ttl_sec` = TTL 구현이 아니라 **제거**(택일 ①) | architecture §5가 "TTL 재적재 없음"으로 확정 → 코드를 문서에 맞춤 (ai#24) |
| 19 | 멀티 세션에서 격리 git worktree 기본 | 단일 워킹트리·인덱스·HEAD 공유로 커밋 오염 (→ T25) |

## 3. 불변식 — 코드만으로 안 드러나는 것 (전부 `기록복원`)

**상태 전이** — FastAPI는 CANCELLED·PENDING 전이 금지(Spring 전용), `retry_count` 증가·재시도 소진 FAILED·`is_deleted` 변경·`core.*` 읽기도 금지. 실패 전이도 `WHERE ...='PROCESSING'` 가드를 유지해 CANCELLED를 덮지 않음. 완료 전이는 `FOR UPDATE`로 잠갔어도 WHERE 가드를 그대로 둠. PROCESSING 만료 expiry는 **Spring rescan 만료값과 동일 값을 주입받음 — ai가 독자적으로 고르지 않음**.

**멱등성·동시성** — guarded UPDATE의 **rowcount 0은 예외가 아니라 정상 종료**. 중복 요청을 이것으로 흡수하며 분산 락·큐를 두지 않음. 사전 점검은 **비용 절감일 뿐 정합성을 보장하지 않음**(통과 직후 Spring이 삭제 가능). 부분 재개 조건은 `embedding_status==COMPLETED` **AND** `embedding_profile==현재`. Context 불변이라 본문 버전 비교 없음. **2-worker 경합 방어로 keyword 단계에 벡터 fallback 로드**(→ `spec/partial-resume.md §3` 개정).

**트랜잭션 경계** — 저장 불변식: `SELECT ... FOR UPDATE` → **status 재검사** → 쓰기 → COMPLETED. 재검사 실패면 예외가 아니라 **결과 폐기·롤백**. "행 존재"가 아니라 "status가 PROCESSING인가"를 보는 것이 삭제 후 지각 INSERT를 막는 지점. 임베딩 TX와 키워드 TX **분리**(키워드 실패가 COMPLETED 임베딩을 롤백 안 함). `context_embedding` UPSERT의 `SET`에 **`is_deleted`를 절대 포함하지 않음**(넣으면 삭제된 context 임베딩 부활). 키워드 저장은 UPSERT가 아니라 **delete-insert**(재판정 시 집합 통째 교체). INSERT 0행이어도 COMPLETED — **"키워드 없음"≠"미처리"**. `context_keyword_analysis`는 unmatchedConcepts가 비어도 행을 남김. `preset_version`은 요청 전체에 스냅샷 고정. Preset Cache는 기동 1회·재시작으로만 무효화(0행이면 기동 실패). 외부 API 호출은 **트랜잭션 밖·락 없이**. 후보 0건은 정상 완료지만 **context 프로필≠preset 프로필이면 판정 중단(영구 오류)**하며 "키워드 없음"으로 완료하지 않음.

## 4. 테스트 확인 / 미확인 범위

**확인**(`기록복원`): 저수준 27(unit·repo·api) + 파이프라인 19함수/20시나리오 = 46 passed(이후 CI 계약 6 추가로 52). 단언 축 = **호출 횟수**. 동시성 두 커넥션 + `asyncio.gather`, `sleep` 없음. Testcontainers 실 pgvector + `ai_snapshot.sql` + TRUNCATE 격리. E1 로컬 end-to-end 실 GMS(모임 0.56·가족 0.72, 타 유저 차단, 422/401/health). E2 4시나리오(정상/후보0→COMPLETED/CANCELLED 거부/**부분 재개 시 임베딩 호출 0회**). DISTINCT ON 대표 선택 실증(record 40에 context 300·301 → 300).

**미확인 — 세션이 인지한 것**(`기록복원`): 시나리오 21은 BE 소관 제외 · **스냅샷 드리프트의 침묵 구간**(back이 스냅샷 안 고치면 ai가 그 컬럼 안 쓰는 동안 조용히 통과) · **워크플로 변경 PR은 병합 전 검증 불가**(GitHub이 pull_request에 base 워크플로 사용 → 병합 후 첫 실행이 첫 검증. PR 본문에 적었으나 실제로 깨짐) · **로컬 환경이 CI 조건 차이를 가림**(PYTHONPATH가 대표) · **크로스레포 사실 드리프트를 어떤 CI도 안 잡음**(`e3-test-harness.md:36` "back과 일치"가 back 변경 순간 거짓이 됐으나 감지 수단 전무) · CI 러너 digest pull network policy 미검증 · **`unmatchedConcepts`는 eval 하네스가 한 번도 실행한 적 없음**(하네스 스키마에 없고 구현에서 처음 추가). 실 GMS를 자동 테스트가 호출 안 하므로(Fake만) **프로바이더 응답 계약 변화를 테스트가 못 잡음** (`추정`).

## 5. spec ↔ 구현 불일치 — 구현 결함 (정상화 아님, 별도 Bug Task 대상)

> 아래는 **spec이 계약으로 명시한 것을 구현이 지키지 않는** 상태다. "왜 그렇게 했는가"는 근거가 없어 `미복원`이다. **이 문서는 사실만 기록하며, 결함을 설계 판단으로 서술하지 않는다.** 실제 수정은 별도 Bug Task(중앙 발급)에서 다룬다.

| # | spec 계약 | 실제 구현 | 출처 | 왜(판단) |
|---|---|---|---|---|
| A-1 | `failure-recovery.md §3.1`: 최대 2회 재시도(지수 백오프+jitter, 대상=타임아웃·429·5xx·연결실패) | **재시도 없음.** 두 클라이언트 httpx 1회. app 전체 retry/backoff/429 매치 0건 | `직접확인`(client, grep 0건) | `미복원` (의도적 유예인지 누락인지, Spring 재스캔 10분으로 충분하다는 판단이었는지) |
| A-2 | `§2.1`: `429`=Transient | `status>=500`만 Transient, 그 외 non-200 전부 Permanent → **429가 영구 오류로 단계 FAILED** | `직접확인`(`embedding_client._embed_batch`) | `미복원` |
| A-3 | `§2.2`: `400`·`401/403`=Permanent | **모든 non-200을 Transient**(401·400 포함). 인증 실패가 10분마다 무한 재시도 | `직접확인`(`llm_client.judge`) | `미복원` (Embedding과 정반대 비대칭의 근거) |
| A-4 | `§2.2`: "재시도 후에도 스키마 위반"을 Permanent | 파싱 실패→Transient. **재시도가 없어 도달 경로 자체가 없음**(분류표 사문화) | `직접확인`(`llm_client._parse`) | `미복원` |
| A-5 | `§3.2`: 연결·읽기 각각 타임아웃, "두 호출 합+재시도 < PROCESSING 만료 600s" | `60.0`/`90.0` 단일 total | `직접확인` | 값 근거 `미복원` |
| A-6 | `§2.2`: Circuit Breaker | 없음 | `직접확인` | `미복원`(MVP 유예 여부) |
| F-1 | `integration-tests.md §3-8`: 일시적 오류→PROCESSING 유지 단언 | **테스트 없음.** `fakes.py`의 `raise_exc`가 어느 테스트에서도 미사용(grep 0건) → **Transient/Permanent 파이프라인 경로가 한 번도 실행된 적 없음** | `직접확인` | `미복원`(의도적 제외인지 누락인지) |
| F-2 | `§2.2`: 영구 오류→단계 FAILED | 파이프라인 테스트 없음(repo SQL만). service가 PermanentError→`fail()` 부르는 결선 미검증 | `직접확인` | — |
| F-3 | `§4.2`: client는 인터페이스 Fake | client HTTP 계층 **테스트 0건**(상태코드→오류타입 매핑·차원 불일치·`_parse` 미검증). **A절 429 오분류가 잡히지 않은 직접 이유** | `직접확인` | 공백 수용 판단인지 `미복원` |

## 6. 실행 인프라 판단 — 현재상태 `직접확인` + 최초이유 `미복원`

> 아래는 **코드에 결과만 있고 근거가 PR·커밋·문서 어디에도 없다.** 최초 선택 이유를 창작하지 않는다.

- **BackgroundTasks** (`api/internal/v1/context.py:24`) — FastAPI `BackgroundTasks`. **큐·워커풀·동시 실행 상한 없음**(요청 수만큼 태스크가 떠 외부 API 동시 타격). 왜 BackgroundTasks인지·Celery/RQ/asyncio 큐 기각 근거·재시작 시 in-flight 유실과 §3.3의 연결: `미복원`.
- **커넥션 풀** (`core/db.py:25-26`) — `min_size=1, max_size=10` **하드코딩**(설정값 아님). 10의 근거·무제한 백그라운드 동시성과의 풀 고갈 지점·설정 미분리 이유: `미복원`.
- **`core/logging.py`** — `basicConfig`+`getLogger`만. 구조화·상관ID·레벨 주입 없음(§2.1은 "WARN으로 context_id·stage·원인 포함" 규정). 미도입이 판단인지 미착수인지: `미복원`.
- **오류 타입 4종** — `PermanentError`/`TransientError`/`PersistDiscarded`/`ProfileMismatchError`. spec은 "두 종류"만. 폐기를 예외 제어흐름으로 구현한 판단: `미복원`.
- **`SharedSecretMiddleware`** (`core/security.py:22`) — `!=` 단순 비교(**상수시간 아님**, `hmac.compare_digest` 미사용), `/health` 무인증. 타이밍 공격을 내부망 전용이라 수용한 판단인지 미인지인지: `미복원`.
- **Profile 정합 검사** — `token not in self.embedding_profile`(**부분 문자열 포함**). `15`가 `1536`에 포함되는 오탐/미탐 인지 여부: `미복원`.

## 7. 기술 부채·임시 처리 (`기록복원`)

`ai_snapshot.sql` 수동 동기화(자동 diff는 스택 확대 후 재검토) · back PR 템플릿 체크는 **제안만**(back 소관) · CONTRIBUTING 미작성(합류 직전으로) · 기능별 폴더 분리 보류(architecture §9에 규칙만) · Python 상한 `<3.13` 잠정 · Jira 상태 전환 수동(-19 미완) · **`conftest.py:23`이 `0.8.1`인 채로 남음**(back·운영은 0.8.5, 정합은 코드 별건). 사전 검사가 **두 번의 별도 커넥션 조회**(TOCTOU 창 2개)로 나뉜 사실도 spec(§4.1 1회)과 어긋나나 근거 `미복원`.

## 8. 미복원 항목 (창작 금지, 여기 확정)

E3 계획 파일 본문(20시나리오→19함수 매핑 기준) · `AskUserQuestion` 선택지 원문(asyncpg 대안이 **SQLAlchemy였다는 것도 `추정`**) · stacked PR(#5→#6) 선택 이유(사용자 선택 사실만) · Bash 도구 간헐 차단 원인 · `.handoff/e3.md`가 `chore/handoff-docs`에 커밋된 경위 · A/B/F절 각 판단 이유 · 타임아웃 60/90·다중 워커 스냅샷 스큐 검토 여부·상수시간 비교·풀 10.

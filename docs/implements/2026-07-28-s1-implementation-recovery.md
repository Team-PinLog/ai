# S1 구현 판단 맥락 복원 — 종료된 세션의 설계 선택 19건·불변식·spec 불일치를 기록에서 복원했다

- **상태**: 완료 (복원)
- **날짜**: 2026-07-28
- **유형**: 구현 (판단 맥락 복원)
- **관련 PR/커밋**: ai#5·#6(E1·E2) · #3(eval) · #7·#8(문서) · #10·#11(contextId) · #14·#16·#17·#18(E3) · #20(주석 정정) · #24(PR2 반영+dead config)
- **근거 원본**: `pinlog/.claude/state/S1-RECOVERY-PACKET.md`(종료된 S1 세션의 transcript 이벤트 1,245건을 정독한 기록) · `S1-DOC-GAP-TABLE.md`(origin/main 의 코드·문서 전수 대조)

> 종료된 S1 세션(AI 파트 작업)이 남긴 구현 판단을 영구 보존하는 문서다. 코드와 spec 만으로는 드러나지 않는 "왜 그렇게 했는가"가 대상이다.
> 각 서술에는 출처를 표기한다. `기록복원`(transcript 에서 확인) · `직접확인`(origin/main 코드에서 확인) · `문서` · `추정` · `미복원`.
> 근거가 없는 항목은 문장을 채우지 않고 `미복원`으로 확정한다. 원 구현자의 판단 이유를 창작하지 않기 위해서다.

## 1. 구현 범위·최종 구조

구현된 엔드포인트는 `POST /internal/v1/search`(동기), `POST /internal/v1/context/process`(202 접수 후 백그라운드 처리), `/health` 다. 전부 `/internal/v1/*` 내부 전용이고 공유 시크릿 미들웨어를 거친다. 기동 시 `bootstrap/load_presets` 가 keyword_preset 임베딩 27건을 적재한다. `tools/keyword_eval` 오프라인 평가 하네스가 앱 구현보다 먼저 존재했고, 그 하네스로 판정 모델을 확정한 뒤 앱으로 포팅했다. (`기록복원`)

계층은 `api → service → {repository, cache, client}` 단방향이다. repository 는 rowcount 만 반환하고 중단 여부의 판단은 service 가 한다. client 는 DB 를 모르고, repository 는 외부 API 를 모른다. (`기록복원`; 계약 architecture §3 준수)

## 2. 비자명한 설계 선택 19건 (전부 `기록복원`)

| # | 선택 | 근거 |
|---|---|---|
| 1 | asyncpg + 원시 SQL. ORM 미사용 | 계약이 SQL 본문을 정본으로 명시하므로 그대로 옮기는 것이 계약 준수에 유리하다. architecture 는 "async 세션"만 규정한 열린 결정이었으므로 `AskUserQuestion` 으로 사용자가 확정했다 |
| 2 | 판정 LLM = `gemini-2.5-flash` + `responseSchema` + `thinkingBudget=0` | eval C-2 실측 결과다. 가장 빠르고(1.12s) 토큰이 가장 적고(25,314) 스키마 위반이 0건이었다 |
| 3 | function-calling 대신 native `responseSchema` | gemini-2.5-flash 가 function-calling 에서 malformed 응답을 반환했다 |
| 4 | `keywordId` 를 JSON-schema enum 으로 제약하지 않고 후처리 필터로 멤버십을 강제 | (근거 기록 없음) |
| 5 | `/search` 집계를 `GROUP BY+MAX` 에서 `DISTINCT ON (record_id)` 로 변경 | Record 별 최고 유사도의 행 자체를 골라야 그 행의 context_id 를 대표값으로 반환할 수 있다. 계약과 시그니처는 바꾸지 않았다 |
| 6 | `SET search_path = ai, public` | `vector` 타입이 public 스키마에 있어서 `ai` 만 고정하면 VECTOR 타입 해석과 `register_vector` 가 실패한다. public 을 넣어도 core 는 여전히 경로 밖이다 (T21) |
| 7 | pgvector 이미지 태그 고정, 롤링 태그 `pg16` 금지 | 롤링 태그는 재빌드 시 마이너 버전이 조용히 바뀌어 CI 가 비결정적이 된다. ANN 인덱스를 쓰지 않으므로 "당장 깨질 위험은 낮다"고 자평했지만, 통일 비용이 0이라 고정을 채택했다. 정본은 운영 이미지이고 ai 테스트가 그것을 따라간다 |
| 8 | Python 3.12 + 상한 `<3.13` | 로컬/CI/미래 합류자의 환경이 3개로 갈라지는 것이 최악이다. 상한이 없으면 합류자가 3.14 로 또 갈라진다. 3.12 를 고른 근거는 GraphRAG 전제다(torch/transformers/igraph 는 최신 Python wheel 이 수개월 지연된다). 버전 고정 위치는 5곳이다 |
| 9 | 신규 `ci.yml` 생성 대신 기존 `ai-ci.yml` 수정 | 워크플로 중복 방지. 기존 워크플로가 이미 3.12 였으므로 lock 전환·dev deps·Jira 검증 스텝만 추가했다 |
| 10 | Jira 키 검증은 PR 제목만 대상 | squash 병합이므로 PR 제목이 최종 커밋 메시지가 된다 |
| 11 | 브랜치 보호는 required check 만 두고, 필수 리뷰 0·`enforce_admins:false` | 1인 레포의 self-merge 와 긴급 대응 여지를 위해서다. 적용 시점을 "CI 그린 후"로 미룬 판단이 옳았음이 확인됐다. 먼저 켰다면 CI 를 고치는 핫픽스 PR 자체가 막혔을 것이다 |
| 12 | 동시성 테스트에서 `sleep` 금지, `on_call` 훅 사용 | Fake 의 `on_call` 안에서 `raw_connect` 로 다른 커넥션을 열어 CANCELLED 를 주입하고 `asyncio.gather` 로 경합을 재현한다 |
| 13 | Fake 는 인터페이스 레벨로 만들고 호출 횟수를 기록 | 단언의 핵심이 `call_count == 0/1` 이다. 실제 GMS 호출은 금지다 |
| 14 | Profile 문자열 리터럴 금지, `settings` fixture 경유 | (근거 기록 없음) |
| 15 | builders 에 "본문 버전" 인자를 두지 않음 | Context 는 불변이다. 수정 시나리오는 context_id 가 다른 두 State 로 표현한다 |
| 16 | `settings` fixture 에서 `get_settings()` 캐시 재설정 | `main.py` 의 모듈 레벨 `app=create_app()` 이 import 시점에 `.env` 를 캐시하기 때문이다 (→ T26) |
| 17 | conftest 에서 placeholder 환경 변수를 먼저 주입 | CI 에는 `.env` 가 없어 import 시점에 실행이 중단되기 때문이다 |
| 18 | dead config `preset_cache_ttl_sec` 는 TTL 을 구현하지 않고 제거(택일 ①) | architecture §5 가 "TTL 재적재 없음"으로 확정했으므로 코드를 문서에 맞췄다 (ai#24) |
| 19 | 멀티 세션 작업에서는 격리 git worktree 를 기본으로 | 단일 워킹트리에서 인덱스·HEAD 를 공유하면 커밋이 오염된다 (→ T25) |

## 3. 불변식 — 코드만으로 안 드러나는 것 (전부 `기록복원`)

**상태 전이**

- FastAPI 는 CANCELLED·PENDING 으로의 전이를 하지 않는다. 이 두 전이는 Spring 전용이다.
- `retry_count` 증가, 재시도 소진에 의한 FAILED, `is_deleted` 변경, `core.*` 읽기도 금지다.
- 실패 전이도 `WHERE ...='PROCESSING'` 가드를 유지한다. 가드가 없으면 CANCELLED 상태를 FAILED 로 덮어쓸 수 있다.
- 완료 전이는 `FOR UPDATE` 로 잠갔더라도 WHERE 가드를 그대로 둔다.
- PROCESSING 만료 기준값(expiry)은 Spring rescan 의 만료값과 동일한 값을 주입받는다. ai 가 독자적으로 고르지 않는다.

**멱등성·동시성**

- guarded UPDATE 의 rowcount 0 은 예외가 아니라 정상 종료다. 중복 요청을 이것으로 흡수하며, 분산 락이나 큐를 두지 않는다.
- 사전 점검은 비용 절감 수단일 뿐 정합성을 보장하지 않는다. 점검 통과 직후에 Spring 이 삭제할 수 있다.
- 부분 재개 조건은 `embedding_status==COMPLETED` 그리고 `embedding_profile==현재 Profile` 두 가지 모두다. Context 가 불변이므로 본문 버전 비교는 없다.
- 2-worker 경합 방어로 keyword 단계에 벡터 fallback 로드를 뒀다(이에 맞춰 `spec/partial-resume.md §3` 을 개정했다).

**트랜잭션 경계**

- 저장 불변식은 `SELECT ... FOR UPDATE` → status 재검사 → 쓰기 → COMPLETED 순서다. 재검사에 실패하면 예외를 던지는 것이 아니라 결과를 폐기하고 롤백한다.
- 재검사가 보는 것은 "행이 존재하는가"가 아니라 "status 가 PROCESSING 인가"다. 이것이 삭제 후 뒤늦게 도착한 INSERT 를 막는 지점이다.
- 임베딩 트랜잭션과 키워드 트랜잭션은 분리한다. 키워드 저장이 실패해도 COMPLETED 된 임베딩을 롤백하지 않는다.
- `context_embedding` UPSERT 의 `SET` 절에 `is_deleted` 를 절대 포함하지 않는다. 포함하면 삭제된 context 의 임베딩이 되살아난다.
- 키워드 저장은 UPSERT 가 아니라 delete-insert 다. 재판정 시 키워드 집합을 통째로 교체하기 위해서다.
- INSERT 가 0행이어도 COMPLETED 다. "키워드 없음"과 "미처리"는 다른 상태다.
- `context_keyword_analysis` 는 unmatchedConcepts 가 비어도 행을 남긴다.
- `preset_version` 은 요청 전체에 스냅샷으로 고정한다.
- Preset Cache 는 기동 시 1회 적재하고 재시작으로만 무효화한다. 적재 결과가 0행이면 기동에 실패한다.
- 외부 API 호출은 트랜잭션 밖에서, 락 없이 수행한다.
- 후보 0건은 정상 완료다. 그러나 context 의 프로필과 preset 의 프로필이 다르면 판정을 중단하고 영구 오류로 처리한다. "키워드 없음"으로 완료하지 않는다.

## 4. 테스트 확인 / 미확인 범위

**확인** (`기록복원`)

- 저수준 27개(unit·repo·api)와 파이프라인 19함수/20시나리오, 합계 46 passed. 이후 CI 계약 6개가 추가되어 52가 됐다.
- 단언의 축은 호출 횟수다. 동시성은 두 커넥션과 `asyncio.gather` 로 재현했고 `sleep` 은 없다.
- Testcontainers 로 실제 pgvector 를 띄우고 `ai_snapshot.sql` 적용, TRUNCATE 격리.
- E1 은 로컬 end-to-end 를 실제 GMS 로 확인했다("모임" 질의 유사도 0.56, "가족" 0.72, 타 유저 차단, 422/401/health).
- E2 는 4시나리오를 확인했다(정상 / 후보 0건도 COMPLETED / CANCELLED 거부 / 부분 재개 시 임베딩 호출 0회).
- DISTINCT ON 의 대표 선택을 실증했다(record 40 에 context 300·301 이 있을 때 300 을 대표로 반환).

**미확인 — 세션이 인지한 것** (`기록복원`)

- 시나리오 21 은 BE 소관이라 제외했다.
- 스냅샷 드리프트의 침묵 구간이 있다. back 이 스키마를 바꾸고 스냅샷을 안 고쳐도, ai 가 그 컬럼을 쓰지 않는 동안은 테스트가 조용히 통과한다.
- 워크플로를 변경하는 PR 은 병합 전에 검증할 수 없다. GitHub 이 pull_request 이벤트에 base 브랜치의 워크플로를 사용하므로, 병합 후 첫 실행이 첫 검증이 된다. PR 본문에 이 한계를 적어 뒀는데 실제로 깨졌다.
- 로컬 환경이 CI 와의 조건 차이를 가린다. PYTHONPATH 가 대표적인 예다.
- 크로스레포 사실 드리프트를 어떤 CI 도 잡지 않는다. `e3-test-harness.md:36` 의 "back 과 일치한다"는 서술이 back 의 변경 순간 거짓이 됐지만 감지 수단이 없었다.
- CI 러너의 digest pull 이 network policy 에 걸리는지 검증하지 않았다.
- `unmatchedConcepts` 는 eval 하네스가 한 번도 실행한 적이 없다. 하네스 스키마에 없었고 구현에서 처음 추가된 필드다.
- 자동 테스트는 Fake 만 쓰고 실제 GMS 를 호출하지 않으므로, 프로바이더의 응답 계약 변화를 테스트가 잡지 못한다. (`추정`)

## 5. spec ↔ 구현 불일치 — 구현 결함 (정상화 아님, 별도 Bug Task 대상)

> 아래는 spec 이 계약으로 명시한 것을 구현이 지키지 않는 상태다. "왜 그렇게 했는가"는 근거가 없어 `미복원`이다. 이 문서는 사실만 기록하며 결함을 설계 판단으로 서술하지 않는다. 실제 수정은 별도 Bug Task `S15P11A705-121`(외부 API 오류 재시도 및 분류 계약 수정)에서 다룬다.

| # | spec 계약 | 실제 구현 | 출처 | 왜(판단) |
|---|---|---|---|---|
| A-1 | `failure-recovery.md §3.1`: 최대 2회 재시도(지수 백오프+jitter, 대상은 타임아웃·429·5xx·연결실패) | 재시도가 없다. 두 클라이언트 모두 httpx 1회 호출이다. app 전체에서 retry/backoff/429 매치가 0건이다 | `직접확인`(client 코드, grep 0건) | `미복원`. 의도적 유예인지 누락인지, Spring 재스캔 10분이면 충분하다는 판단이었는지 알 수 없다 |
| A-2 | `§2.1`: `429`=Transient | `status>=500` 만 Transient 이고 그 외 non-200 은 전부 Permanent 다. 결과적으로 429 가 영구 오류로 분류되어 단계가 FAILED 가 된다 | `직접확인`(`embedding_client._embed_batch`) | `미복원` |
| A-3 | `§2.2`: `400`·`401/403`=Permanent | 모든 non-200 을 Transient 로 분류한다(401·400 포함). 인증 실패가 10분마다 무한 재시도된다 | `직접확인`(`llm_client.judge`) | `미복원`. Embedding 클라이언트와 정반대인 비대칭의 근거를 알 수 없다 |
| A-4 | `§2.2`: "재시도 후에도 스키마 위반"이면 Permanent | 파싱 실패를 Transient 로 분류한다. 재시도 자체가 없어 이 조항에 도달하는 경로가 없다(분류표가 사문화됐다) | `직접확인`(`llm_client._parse`) | `미복원` |
| A-5 | `§3.2`: 연결·읽기 타임아웃을 각각 두고, "두 호출 합+재시도 < PROCESSING 만료 600s" | `60.0`/`90.0` 의 단일 total 타임아웃뿐이다 | `직접확인` | 값의 근거 `미복원` |
| A-6 | `§2.2`: Circuit Breaker | 없다 | `직접확인` | `미복원`. MVP 유예 여부를 알 수 없다 |
| F-1 | `integration-tests.md §3-8`: 일시적 오류 시 PROCESSING 유지 단언 | 테스트가 없다. `fakes.py` 의 `raise_exc` 가 어느 테스트에서도 사용되지 않는다(grep 0건). Transient/Permanent 파이프라인 경로가 한 번도 실행된 적이 없다 | `직접확인` | `미복원`. 의도적 제외인지 누락인지 알 수 없다 |
| F-2 | `§2.2`: 영구 오류 시 단계 FAILED | 파이프라인 테스트가 없다(repo SQL 만 검증). service 가 PermanentError 를 받아 `fail()` 을 부르는 결선이 미검증이다 | `직접확인` | — |
| F-3 | `§4.2`: client 는 인터페이스 Fake 로 대체 | client 의 HTTP 계층 테스트가 0건이다. 상태코드→오류타입 매핑·차원 불일치·`_parse` 가 미검증이다. A절의 429 오분류가 잡히지 않은 직접적인 이유다 | `직접확인` | 공백을 수용한 판단인지 `미복원` |

## 6. 실행 인프라 판단 — 현재 상태는 `직접확인`, 최초 이유는 `미복원`

> 아래 항목들은 코드에 결과만 있고 그 근거가 PR·커밋·문서 어디에도 없다. 최초 선택 이유를 창작하지 않는다.

- **BackgroundTasks** (`api/internal/v1/context.py:24`) — FastAPI `BackgroundTasks` 를 쓴다. 큐·워커풀·동시 실행 상한이 없어 요청 수만큼 태스크가 떠서 외부 API 를 동시에 호출한다. 왜 BackgroundTasks 인지, Celery/RQ/asyncio 큐를 기각한 근거, 재시작 시 in-flight 작업 유실과 §3.3 의 관계: `미복원`.
- **커넥션 풀** (`core/db.py:25-26`) — `min_size=1, max_size=10` 하드코딩이다(설정값이 아니다). 10 의 근거, 상한 없는 백그라운드 동시성에서 풀이 고갈되는 지점, 설정으로 분리하지 않은 이유: `미복원`.
- **`core/logging.py`** — `basicConfig`+`getLogger` 만 있다. 구조화 로깅·상관 ID·레벨 주입이 없다(§2.1 은 "WARN 으로 context_id·stage·원인 포함"을 규정한다). 미도입이 판단인지 미착수인지: `미복원`.
- **오류 타입 4종** — `PermanentError`/`TransientError`/`PersistDiscarded`/`ProfileMismatchError`. spec 은 "두 종류"만 규정한다. 폐기를 예외 제어흐름으로 구현한 판단: `미복원`.
- **`SharedSecretMiddleware`** (`core/security.py:22`) — `!=` 단순 비교다. 상수시간 비교(`hmac.compare_digest`)를 쓰지 않았고 `/health` 는 무인증이다. 타이밍 공격을 내부망 전용이라는 이유로 수용한 것인지 인지하지 못한 것인지: `미복원`.
- **Profile 정합 검사** — `token not in self.embedding_profile` 로 부분 문자열 포함을 검사한다. `15` 가 `1536` 에 포함되는 식의 오탐/미탐 가능성을 인지했는지: `미복원`.

## 7. 기술 부채·임시 처리 (`기록복원`)

- `ai_snapshot.sql` 은 수동 동기화다. 자동 diff 는 스택 확대 후 재검토한다.
- back PR 템플릿 체크는 제안만 했다(back 소관).
- CONTRIBUTING 은 미작성이다(합류 직전이라 미룸).
- 기능별 폴더 분리는 보류했다. architecture §9 에 규칙만 남겼다.
- Python 상한 `<3.13` 은 잠정이다.
- Jira 상태 전환은 수동이다(-19 미완).
- ~~`conftest.py:23` 이 `0.8.1` 인 채로 남았다(back·운영은 0.8.5)~~ → 해소됨. `S15P11A705-122` 에서 `0.8.5-pg16` + digest 고정으로 정합화했다.
- 사전 검사가 두 번의 별도 커넥션 조회로 나뉘어 있다(TOCTOU 창이 2개). spec §4.1 의 "1회 조회"와 어긋나지만 근거는 `미복원`이다.

## 8. 미복원 항목 (창작 금지, 여기서 확정)

- E3 계획 파일 본문(20시나리오→19함수 매핑의 기준)
- `AskUserQuestion` 선택지 원문. asyncpg 의 대안이 SQLAlchemy 였다는 것도 `추정`이다.
- stacked PR(#5→#6) 선택 이유(사용자가 선택했다는 사실만 남아 있다)
- Bash 도구 간헐 차단의 원인
- `.handoff/e3.md` 가 `chore/handoff-docs` 브랜치에 커밋된 경위
- §5 A/B/F 각 항목의 판단 이유
- 타임아웃 60/90 의 근거, 다중 워커 스냅샷 스큐 검토 여부, 상수시간 비교, 풀 크기 10 의 근거

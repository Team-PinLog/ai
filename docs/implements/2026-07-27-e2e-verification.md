# E2E 검증 — 실제 GMS 경로 전수 확인

- **상태**: 완료
- **날짜**: 2026-07-27
- **관련 PR**: (이 PR)
- **근거 계약**: [spec/integration-tests.md](../spec/integration-tests.md), [spec/keyword-preset.md](../spec/keyword-preset.md), [spec/personal-search.md](../spec/personal-search.md)
- **트러블슈팅**: [troubleshooting/2026-07-27-e2e-env-issues.md](../troubleshooting/2026-07-27-e2e-env-issues.md) (T22~T24)
- **도구**: [tools/e2e/](../../tools/e2e/)

## 무엇을 검증했나

ai#5·#6·#11·#14·#16·#17·#18로 구현이 끝났으나, 검증된 것은 **Fake 기반 46 테스트(계약 위반·경합 방어)뿐**이었다. 실제 GMS 호출, 프리셋 실적재, 실제 임베딩 기반 검색 품질은 한 번도 실행된 적이 없다.

이 세션은 **구현하지 않은 시각**에서 `README.md`와 `docs/spec/`만 보고 로컬 기동을 재현했다. 절차를 미리 알려주지 않은 것은 의도적이며, **문서만으로 기동 가능한지가 검증 대상**이었다. 따라서 막힌 지점 자체가 주 산출물이다.

범위: ①로컬 환경 기동 ②`/internal/v1/context/process` 실경로 ③`/internal/v1/search` 실경로·품질 ④Docker 빌드·기동. 추가로 매칭 하네스(`tools/keyword_eval/`)와 운영 코드의 동등성을 실측했다.

**프로덕션 코드 변경 없음.** 이 PR은 문서와 검증 도구만 추가한다.

## 문서 마찰 — 이 세션의 주 산출물

> **아래는 검증 시점(2026-07-27 오전)의 상태입니다.** F1·F2·F4·F5와 판정 비결정성 명시는
> 이 검증의 발견을 받아 **-59 세션이 ai#22로 이미 반영**했습니다(F6은 이후 ai#24로 해결). 각 항목에 반영 결과를 병기하며,
> 전체 대응은 [발견 처리](#발견-처리) 표를 참조하십시오. 기록을 남기는 이유는 보존 원칙(정정하되
> 삭제하지 않음)과, **"문서만으로 기동 가능한가"의 답이 당시 '아니오'였다는 사실 자체가 산출물**이기
> 때문입니다.

### F1 — README 2단계에 절차가 없다 (최대 마찰)

```
# 2. ai 스키마 생성 — back Flyway(V1/V100/V101)를 적용해 ai.* 테이블 마련
#    (ai 레포는 Migration을 실행하지 않는다)
```

**"적용해"의 방법이 없다.** back 레포 경로도, gradle 명령도, SQL 직접 적용 대안도 없다. 합류자는 여기서 반드시 멈춘다. 이번 검증에서는 `back/src/main/resources/db/migration/`을 직접 찾아 `docker exec psql`로 순차 적용했고, 이 경로 탐색이 전체에서 가장 오래 걸린 판단이었다.

부수 문제: back에는 **`V102__feed_event.sql`도 존재**하는데 README는 V1/V100/V101만 적는다. core 소관이라 제외했으나 문서에 판단 근거가 없어 스스로 결정해야 했다.

> **반영됨(ai#22)**: README 2단계에 back 마이그레이션 파일 위치, 컨테이너 psql 적용 루프, V102 제외 근거가 추가됐다.

### F2 — DSN이 3중으로 어긋난다

| 출처 | port | user / password |
|---|---|---|
| `README.md` 1단계 `docker run` | 5433 | `pinlog` / `pinlog` |
| `.env.example` (3단계가 복사하라는 파일) | **5432** | **`ai_app`** / `CHANGME` |
| `back/compose.yaml` (1단계가 대안으로 지목) | **15432** | `pinlog` / **`pinlog-local`** |

README 1단계를 실행하고 3단계(`cp .env.example .env`)를 그대로 따르면 **연결 실패**한다. 3단계에 "DSN 주입"이라 적혀 있어 의도된 여지일 수 있으나, 어느 값이 정답인지 문서가 정하지 않는다.

> **반영됨(ai#22)**: `pinlog:pinlog@localhost:5433/pinlog`로 통일하고 `.env.example` 기본값을 일치시켰다. back `compose.yaml`과 혼용하지 말라는 경고도 명시됐다.

### F5 — Docker는 build만 문서화돼 있다

README에 `docker build`만 있고 `docker run` 예시가 없다. 이미지는 `.dockerignore`로 `.env`를 제외하므로 **13개 환경변수를 전부 주입해야 하는데 그 목록도, host DB 접근 방법도 없다**. 이번에 구성해 검증한 명령은 아래 [Docker](#docker) 절에 남긴다.

> **반영됨(ai#22)**: 검증한 `docker run` 명령이 README Docker 절에 그대로 추가됐다.

### F6 — `PRESET_CACHE_TTL_SEC`은 정의만 있고 읽는 곳이 없다

`app/core/config.py`·`.env.example`에 존재하고 `spec/architecture.md` §5가 TTL 기반 재적재로 서술하지만, `PresetCache`에 reload 경로가 없다. **프리셋은 프로세스 수명 동안 고정**이다. 문서-구현 불일치.

> **해결됨(ai#24)**: `preset_cache_ttl_sec`를 `config.py`·`.env.example`에서 제거(ai#24)하고 `architecture.md §5`를 "재시작으로만"으로 정정(ai#21)했다. 문서-구현 정합.

### 문서대로 작동한 것

1·3·4·5단계의 명령 자체, `pytest`(46개 전원 통과), `docker build`. 또한 `tests/schema/ai_snapshot.sql`을 back `V100__ai_tables.sql`과 diff한 결과 **테이블 정의 차이 없음**(주석·V1/V101 병합분만 차이) — 스냅샷이 자체 경고하던 드리프트는 발생하지 않았다.

## 판정 기준 결과

### 프리셋 적재 — 통과

`python -m app.bootstrap.load_presets` (실제 GMS 임베딩 1배치 호출)

```
total | embedding_null | profile_kinds |                   profile                    | dims | active
   27 |              0 |             1 | openai-text-embedding-3-small-1536-cosine-v1 | 1536 |     27
```

27행 · embedding 전부 NOT NULL · profile 1종이며 `settings.embedding_profile`과 일치. 범주 배분도 설계대로(COMPANION 6 / ACTIVITY 8 / ATMOSPHERE 7 / SITUATION 6).

### 파이프라인 — 통과

Context 8건 투입, **전부 6.0초에 두 status COMPLETED 도달**. PENDING/PROCESSING 잔류 0.

| ctx | 투입 성격 | 판정 결과 |
|---|---|---|
| 1001 | 친구·활기찬 | `WITH_FRIENDS(1.0)` `MEAL(0.8)` `LIVELY(1.0)` |
| 1002 | 혼자·작업·조용 | `ALONE` `STUDY_WORK` `QUIET` (전부 1.0) |
| **1003** | **부대시설 이야기만** | **키워드 0개** + unmatched 4개 |
| 1004 | 프리셋에 없는 개념 | 키워드 0개 + unmatched `["반려견 동반"]` |
| 1005 | 연인·기념일·야경 | `WITH_PARTNER` `ANNIVERSARY` `VIEW_GOOD` |
| 1006 | 비·아늑·레트로 | `RAINY_DAY` `COZY` `RETRO` |
| 1007 | 5001의 두 번째 Context | `EXHIBITION` |

- **후보 밖 keyword_id: 0건** — `context_keyword LEFT JOIN keyword_preset`으로 검출
- **keyword 0개 정상 처리** — 1003·1004가 COMPLETED + `context_keyword` 0행
- **`unmatched_concepts` 저장 확인** — 1003 → `["넓은 주차장","깨끗한 화장실","친절한 직원 응대","합리적인 가격"]`, 1004 → `["반려견 동반"]`. `preset_version=1`, `model_profile=gemini-2.5-flash`도 정상 기록

1003이 특히 의미 있다. 부대시설 4개를 **억지로 키워드에 넣지 않고 unmatched로 분류**했다 — `llm_client.SYSTEM`의 부대시설 제외 규칙과 `unmatchedConcepts` 지시가 둘 다 실경로에서 작동한다는 증거다.

**미검증 1건**: `BLOCKED` 프리셋 제외 로직은 `data/keyword_preset.yaml`에 BLOCKED가 0건(PUBLIC 25 / PRIVATE_ONLY 2)이라 실데이터로 검증 불가하다. Fake 테스트에만 존재한다.

## 검색 품질

### 계약 방어선 — 3종 통과

정상 200 · Profile 불일치 **422**(임베딩 미호출) · 시크릿 누락 **401**.

### 관련 질의 6건 전부 의도 Context가 1위

기준은 "상위 3위 내"였으나 전부 1위였다.

| 질의 | 1위 | similarity |
|---|---|---|
| 비 오는 날 아늑하게 있을 만한 데 | 을지로 골목 찻집 (1006) | 0.6740 |
| 강아지 데려갈 수 있는 곳 | 한강 근처 카페 (1004) | 0.6430 |
| 친구들이랑 시끌벅적하게 놀 만한 곳 | 연남동 술집 (1001) | 0.6417 |
| 혼자 조용히 작업하기 좋은 카페 | 성수동 카페 (1002) | 0.5816 |
| 기념일에 야경 보면서 식사할 곳 | 남산 뷰 레스토랑 (1005) | 0.5266 |
| 주차 편한 곳 | 외곽 식당 (1003) | 0.5263 |

### 분리도 — 두 분포가 겹치지 않는다

```
관련 질의 top1 : min 0.5263 · max 0.6740 · avg 0.5989
무관 질의 top1 : min 0.2362 · max 0.3143 · avg 0.2752
간격(관련 min − 무관 max) = +0.2120
```

**검색에 하한을 적용하지 않는 설계가 옳다는 실측 근거를 얻었다.** 무관 질의 "자동차 엔진오일 교환 정비소"의 top1이 **0.3143으로 `SIMILARITY_FLOOR=0.30`을 넘는다.** 즉 검색에 0.30을 걸었더라도 이 무관 질의는 걸러지지 않았다. `spec/personal-search.md` §6의 "기본은 적용하지 않는다"를 지지하는 동시에, **0.30은 검색용 컷오프로 부적절한 값**임을 보여준다(그 값은 Keyword 후보 선정용으로 산출된 것이다 — `tools/keyword_eval/REPORT.md`). 하한을 도입한다면 별도 튜닝이 필요하다.

### DISTINCT ON 대표 선택 — 통과

record 5001은 Context 1001·1007 두 건을 가진다. 질의에 따라 대표가 바뀌고, **두 경우 모두 1회만 등장**한다.

```
"친구들이랑 시끌벅적하게 놀 만한 곳"  → rec=5001 대표 ctx=1001 (0.6414)
"사진 전시 구경"                     → rec=5001 대표 ctx=1007 (0.5962)
```

### 사용자 격리 — 통과

user 9001 검색 결과에 9002 소유 ctx 1008 미노출. 9002 검색에는 1008만.

### 부수 관찰

"주차 편한 곳"이 ctx 1003을 1위로 찾는다. **키워드는 0개인데 검색은 된다.** 임베딩 검색과 키워드 판정이 독립적으로 동작한다는 뜻이고, 제품상으로도 옳다 — 부대시설은 Keyword가 아니지만 사용자가 그 이유로 다시 찾을 수는 있다.

## 동등성 실측 — 하네스 ↔ 운영

### 전제부터 거짓이었다

당초 질문은 "하네스와 운영 코드가 **같은 프롬프트로 같은 결과**를 내는가"였다. 코드 정독 결과 전제가 성립하지 않았다.

- `tools/` 전체에 **`from app...` import가 0건** — 하네스는 운영 코드를 공유하지 않는 복사본이다
- 프롬프트가 이미 다르다: 운영 `app/client/llm_client.py`의 `SYSTEM`에만 `unmatchedConcepts` 지시 1줄이 있다
- responseSchema도 다르다: 운영만 `unmatchedConcepts` 속성 보유, 하네스는 `enum` 옵션 보유

그래서 질문을 **"그 차이가 결과를 바꾸는가"**로 재정의해 실측했다.

### 설계 — 비결정성 기준선을 먼저 잰다

LLM 판정은 흔들릴 수 있으므로, 두 경로의 차이를 재기 전에 **운영 경로 자체의 흔들림**을 먼저 측정했다. 이것을 하지 않으면 모든 불일치를 프롬프트 차이로 오귀속하게 된다.

1. 운영 판정을 같은 입력으로 2회 → 자체 재현성
2. 후보 집합 비교 — 운영(DB 적재 벡터 + `_topk`) vs 하네스(자체 임베딩 + `argsort`)
3. 판정 비교 — **동일 후보**를 두 경로에 투입(프롬프트·스키마만 다르게)

샘플 10건(`tools/keyword_eval/samples.yaml`).

### 결과 — 후보 9/10 · 판정 9/10

불일치 2건을 각각 반복 측정해 원인을 귀속시켰다.

**후보 불일치(샘플 00) — 동점 경계의 임베딩 잡음**

```
운영    10위 205 (0.301145)   11위 106 (0.300731)
하네스  10위 106 (0.301547)   11위 205 (0.301097)
        차이 0.0004 · 9·12·13위는 양쪽 완전 일치
```

두 경로가 프리셋을 각각 따로 임베딩하기 때문에 생기는 **API 미세 잡음**이며, K=10 경계에서만 순위가 뒤집혔다. 선정 로직 자체는 동일하다.

**판정 불일치(샘플 05) — 양쪽 모두 흔들린다**

각 경로 5회 반복:

```
"팀 회식으로 갔는데 룸 있어서 편했음"   후보 [403,104,405,201,101,204,305,202,205,206]
  운영   202(식사) 포함 2/5
  하네스 202(식사) 포함 3/5
```

**계통적 차이가 아니라 경계 사례의 확률적 편향이다.**

### 결론

**프롬프트 1줄 차이가 selected 결과를 바꾼다는 증거는 없다.** 다만 코드가 분리돼 있다는 구조적 위험은 그대로이며, 프롬프트 사본이 3개(운영 `llm_client.py` / 하네스 `test_c_judge.py` / `tools/keyword_eval/prompts/keyword_judgment.md`)이고 그 markdown은 운영이 쓰지 않는 `enum`·`minimum`을 여전히 적고 있다.

### 부수 발견 — 판정은 결정적이지 않다

`thinkingConfig.thinkingBudget=0`인데도 경계 샘플이 40~60%로 흔들린다. 저장이 delete-insert이므로 **같은 Context를 재판정하면 저장된 키워드가 실제로 바뀐다.**

다만 **재판정이 일어나는 경로가 실질적으로 거의 없다**:

| 경로 | 재판정 여부 |
|---|---|
| 부분 재개 | keyword가 COMPLETED면 `try_start`가 0을 반환해 건너뜀 → 재판정 없음 |
| Context 수정 | 새 `context_id` → "바뀐 것"이 아니라 "새로 붙은 것" |
| 재스캔 회수 | 미완료 단계만 대상 → 재판정이 아니라 최초 판정 |
| **운영 재처리(M3)** | Spring의 `COMPLETED → PENDING`(Profile 전환·프리셋 재분류, [state-machine.md](../spec/state-machine.md) §49행) — **입력이 바뀌므로 결과 변화가 당연** |

따라서 **현행 유지(허용)로 판단**한다. 다만 검증 시점에는 계약 어디에도 "판정이 결정적이지 않다"는 사실이 명시돼 있지 않았다 → **-59가 [keyword-preset.md](../spec/keyword-preset.md) §4.4에 명시**했다(ai#22).

## F2b 권한 경계 실증 — `-61` 근거

F2에서 로컬이 `.env.example`이 처방한 `ai_app`이 아니라 `pinlog`으로 돈다는 것을 발견했고, 그 파급을 실증했다.

```sql
SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AS is_superuser,
       has_schema_privilege(current_user,'core','USAGE'), has_schema_privilege(current_user,'core','CREATE');
-- pinlog | t | t | t

CREATE TABLE core.boundary_probe(id int);   -- CREATE TABLE
INSERT INTO core.boundary_probe VALUES (1); -- INSERT 0 1
```

접속 롤이 **슈퍼유저**이고, **`ai_app` 롤은 DB에 존재조차 하지 않는다**(`pg_roles`에 `pinlog` 단독). FastAPI가 사용하는 바로 그 DSN으로 `core`에 DDL·DML이 통과한다.

> **"FastAPI는 `core.*`에 접근하지 않는다"(README:10, [architecture.md](../spec/architecture.md) §7)는 계약이 로컬에서 검증되지 않는다.** 위반해도 성공하기 때문이다. 코드 리뷰로만 지켜지고 있으며 DB가 강제하지 않는다.

`search_path = ai, public`이 `core`를 경로 밖에 두는 1차 방어선이지만, 스키마 한정 참조(`core.foo`)는 그대로 통과한다. **인프라 티켓 `S15P11A705-61`(ai 전용 DB role)의 실증 근거**다. 프로브 테이블은 즉시 DROP했다.

## Docker

빌드 360MB, 기동 정상, 기동 로그에 `preset cache loaded: 27 presets`. 컨테이너 경유 실호출까지 확인 — `/search` 200(실제 GMS 임베딩), Profile 불일치 422, 시크릿 누락 401.

README에 없어 이번에 구성해 검증한 명령(F5 → -59 인계 대상):

```bash
docker run -d --name pinlog-ai -p 8000:8000 \
  --env-file .env \
  -e DATABASE_URL="postgresql://pinlog:pinlog@host.docker.internal:5433/pinlog" \
  --add-host=host.docker.internal:host-gateway \
  pinlog-ai
```

`--env-file .env` 뒤에 `-e DATABASE_URL`을 두어 host 접근용으로 덮어쓴다(컨테이너의 `localhost`는 자기 자신이므로 `.env`의 DSN을 그대로 쓰면 연결 실패).

## 검증 과정에서의 판단

결과만으로는 드러나지 않는 두 판단을 남긴다.

### ① `register_vector` 미등록 — 자기 스크립트 문제임을 어떻게 알았나

동등성 스크립트가 프리셋 캐시 적재에서 터졌다.

```
ValueError: could not convert string to float: '[0.05609131,0.008399963,...]'
```

**제품 결함으로 오인하기 쉬운 형태**였다. 벡터가 문자열로 넘어오니 `PresetCache._to_array`가 실패한 것이고, 같은 코드가 서버에서는 이미 정상 동작 중이었다.

판단 근거는 **같은 코드가 두 경로에서 다르게 동작한다**는 사실이었다. `app/main.py`의 lifespan은 정상적으로 27건을 적재했는데(기동 로그로 확인됨) 내 스크립트만 실패했다. 그러면 차이는 코드가 아니라 **연결 방식**에 있다. `app/core/db.py`를 보니 커넥션 초기화에서 `SET search_path = ai, public` + `register_vector(conn)`를 수행하는데, 내 스크립트는 `asyncpg.connect`를 직접 호출해 그 초기화를 건너뛰고 있었다.

교훈은 T21과 같은 계열이다 — **pgvector는 커넥션마다 타입 등록이 필요**하고, 앱이 그것을 `Database`에 캡슐화해 두었다. 우회하면 재현된다. 해결은 스크립트가 `Database`를 그대로 쓰도록 바꾼 것이며, 이는 **더 충실한 검증**이기도 하다(앱과 동일한 연결 설정을 쓰게 되므로). 시딩 스크립트에서 재발할 수 있어 [T23](../troubleshooting/2026-07-27-e2e-env-issues.md)으로 기록했다.

### ② 동등성 결론이 뒤집힌 과정

1차 실행에서 판정 9/10 일치, 불일치 1건(샘플 05에서 하네스만 `202` 추가). 같은 실행에서 **운영 자체 재현성은 10/10**이었다.

이 두 숫자만 보면 결론은 명확해 보인다 — 운영은 결정적인데 하네스만 다른 답을 냈으니, **차이의 원인은 프롬프트 1줄**이다. 실제로 그렇게 적을 뻔했다.

문제는 재현성 10/10이 **샘플 05를 2회 뽑아 우연히 같은 값이 나온 것**을 포함한다는 점이다. 후속 반복 측정 결과 샘플 05는 운영에서도 202를 2/5로 뽑는다. 2회 추출에서 같은 답이 나올 확률은 약 52%이므로, **10/10은 운이었다.**

반복 측정 후 실제 분포는 운영 2/5 · 하네스 3/5 — **양쪽 모두 흔들리며 차이는 통계적으로 무의미**하다. 결론이 "프롬프트 1줄 차이 때문"에서 "양쪽 비결정성"으로 뒤집혔다.

> **비결정성 기준선을 먼저 재지 않았다면 오귀속했을 것이다.** 그리고 기준선을 재더라도 **표본이 작으면 기준선 자체가 거짓말을 한다.** 불일치가 나온 사례는 그 사례에 대해 반복 측정해야 한다.

## 시딩 가능 범위 판단

- **가능 — `/search` 시연은 `ai` 스키마만으로 100% 성립한다.** 실측으로 확인했다. 검색 쿼리는 `ai.context_embedding` + `ai.context_ai_state`만 조인하고 `core`를 읽지 않으며, 이번 검증이 정확히 그 상태(`core` 테이블 0개)에서 돌았다.
- **가능 — 키워드 파이프라인 시연도 동일**. `placeMeta`는 MVP 미사용이라 Place 데이터가 없어도 무관하다.
- **제약 — 화면에는 id 숫자만 나온다.** 본문 조립은 Spring이 `core`에서 하는 구조다(`spec/personal-search.md` §6). → **매핑 파일 방식**으로 해결한다. 이번 검증에서 [`tools/e2e/e2e_contexts.yaml`](../../tools/e2e/e2e_contexts.yaml)이 `context_id`/`record_id` ↔ 본문·장소명을 들고 있고, 검색 결과 출력에 장소명을 붙이는 데 실제로 사용했다. 그대로 확장하면 된다.
- **불가 — 피드 시연.** core 도메인 미착수(백엔드가 member 엔티티 스캐폴딩 단계, core 마이그레이션 V2~ 미착수).
- **비권장 — core 테이블 직접 생성.** ①계약 위반(README:10·`architecture.md` §7이 core 소유·접근을 배제) ②back의 V2~ 구간과 Flyway 충돌 → 로컬 DB 통째 재생성 위험 ③**이득이 없다** — 매핑 파일로 동일한 시연 효과를 충돌 위험 0으로 얻는다. 이번 세션이 그것을 실증했다.

## 발견 처리

| 발견 | 성격 | 처리 | 상태 |
|---|---|---|---|
| F1 README 2단계 절차 없음 | 문서 누락 | -59 → README 2단계에 파일 위치·psql 루프·V102 제외 근거 | **반영됨** (ai#22) |
| F2 DSN 3중 불일치 | 문서 오류 | -59 → `5433`/`pinlog`로 통일, `.env.example` 일치, 혼용 경고 | **반영됨** (ai#22) |
| F5 `docker run` 미문서화 | 문서 누락 | -59 → 검증한 명령을 README Docker 절에 추가 | **반영됨** (ai#22) |
| F6 `PRESET_CACHE_TTL_SEC` 미사용 | 문서-구현 불일치 | -59 → 설정·문서·`.env.example`에서 제거(ai#24) | **해결됨** (ai#24) |
| F4 검색 하한 실측 근거 | 근거 보강 | -59 → `personal-search.md` §6에 0.3143·간격 +0.2120 기재 | **반영됨** (ai#22) |
| 판정 비결정성 | 계약 명시 필요 | 현행 유지(허용) 결정 + -59 → `keyword-preset.md` §4.4 신설 | **반영됨** (ai#22) |
| **F2b 권한 경계 미검증** | 인프라 | **`S15P11A705-61`** (ai 전용 DB role) — 근거는 이 문서 | **미해소** |
| 하네스-운영 코드 분리 | 구조 | 실측상 결과 차이 없음 — 프롬프트 사본 3개는 잔존, 통합은 별건 | 기록 |
| BLOCKED 제외 실데이터 미검증 | 커버리지 갭 | 프리셋에 BLOCKED가 생기면 자연 해소 | 기록 |
| T22~T24 환경 이슈 | 재현 가능 | [troubleshooting](../troubleshooting/2026-07-27-e2e-env-issues.md) | 이 PR |

## 검증

```
pytest -q                                    46 passed
python -m app.bootstrap.load_presets         OK: 27 presets upserted (실제 GMS)
uvicorn app.main:app --port 8000             preset cache loaded: 27 presets, /health 200
Context 8건 → /context/process               6.0s에 두 status 전부 COMPLETED
후보 밖 keyword_id                            0건
docker build -t pinlog-ai .                  360MB
docker run (위 명령)                          /health 200, /search 200(실 GMS), 422, 401
```

## 남은 것

- 시딩 설계(사용자 수·사용자당 맥락 수·취향 분산·매핑 파일 확장) — 별도 진행
- `BLOCKED` 프리셋 실데이터 검증 — 프리셋에 BLOCKED가 생기면 자연 해소
- 하네스·운영 프롬프트 단일화 — 결과 차이는 없으나 사본 3개는 남아 있음

# 시연 도구 결함 3건 — 오류 없이 지나가던 실패를 실행 전에 차단·보고하게 했다

- **티켓**: S15P11A705-198
- **상태**: 완료
- **날짜**: 2026-07-31
- **선행**: [데모 시딩](2026-07-29-demo-seeding.md) (`-58`) · [실데이터 E2E](2026-07-30-real-data-e2e.md) (`-174`) · [임베딩 4조건](2026-07-31-embedding-grid.md) (`-191`)

## 이 문서가 다루는 것

세 결함은 성격이 같다. 이미 한 번씩 실제 사고를 냈고, 셋 다 발생 시점에는 아무 오류
없이 지나갔다.

| # | 결함 | 결과 |
|---|---|---|
| 1 | `seed.py` 가 `email` 을 안 채웠다 | back 기동이 Flyway V6 에서 실패한다. 시딩 시점에는 문제가 없었다 |
| 2 | `--reset` 이 고아 행을 남긴다 | 저장 비용 평균이 8행만큼 틀렸다. 아무도 알려주지 않았다 |
| 3 | worktree 에서 JWT 키가 갈라진다 | 전 요청이 401 인데 back 로그에 아무것도 남지 않는다 |

값 자체는 `-191` 이 이미 고쳤다(`email` 채움, 고아 8행 삭제). 여기서 고칠 것은 재발
경로다.

---

## 1. 조사 — 무엇이 실제로 사실인가

### 1.1 로컬 DB 는 두 개이고, `.env` 는 시연 DB 를 가리키지 않는다

조사 도중 먼저 드러난 것이 이것이다. 컨테이너가 여섯 개 떠 있고 그중 pgvector 가 둘이다.

| 컨테이너 | 포트 | 정체 | `core.*` | `ai.keyword_preset` | `ai.context_embedding` |
|---|---|---|---|---|---|
| `pinlog-demo-postgres-1` | **15432** | 시연·E2E 정본 | 8테이블 · Flyway V1~V6·V100~V102 | 27 | 37 |
| `pinlog-pgv-e2e` | **5433** | 07-27 E2E 하네스 잔재 | **스키마만 있고 테이블 0개** | 27 | 8 (`ctx 1001~1008`) |

`ai/.env` 의 `DATABASE_URL` 은 `:5433` 이다. 시연
절차([`-174` §7](2026-07-30-real-data-e2e.md))는 매 명령에 `DATABASE_URL=...:15432`
를 명시적으로 덮어써서 돌린다. 즉 기본값이 정본을 가리키지 않으며, 환경변수를
빠뜨리면 도구가 아무 표시 없이 다른 DB 에 붙는다.

`-198` 계약이 "프리셋 27행·임베딩 37행" 이라고 적은 것은 `:15432` 기준이고 그것은 맞다.
`:5433` 의 8행은 07-27 하네스가 만든 합성 `context_id`(1001~1008)다.

> 이것은 결함 3(키가 갈라진다)과 같은 계열이다. 환경이 갈라졌는데 아무도 말해 주지
> 않는다.

### 1.2 결함 2 의 실물은 임베딩이 아니라 `context_keyword_analysis` 다

`-191` 이 지운 고아 임베딩 8행은 실제로 사라졌다(`:15432` 고아 임베딩 0). 그런데 같은 원인의
훨씬 큰 잔재가 **지금도 남아 있다.**

```
ai.context_keyword_analysis   259행   그중 고아 222행   (context_id 1~251, 현재 context 는 252~288)
ai.context_ai_state            37행   고아 0
ai.context_embedding           37행   고아 0
ai.context_keyword             72행   고아 0
```

`reset()` 의 삭제 목록에 `ai.context_keyword_analysis` 가 아예 없다.
`ai_state`·`embedding`·`keyword` 셋만 지운다. 그래서 시딩을 반복할 때마다 이
테이블만 단조 증가했다.

이건 논쟁거리가 아니다. 지우는 대상이 `demo-seed` member 소유 context 로 한정되므로
"남의 데이터" 문제가 아니고, 순수한 누락이다.

논쟁거리는 남은 222행 쪽이다(§3.2).

### 1.3 결함 1 의 근본 원인은 "우리가 안 채우는 컬럼이 있다는 것을 몰랐다" 는 것

`V4__social_account.sql` 은 `email` 을 nullable 로 만들었고 주석까지 달아
두었다(*"공급자 미제공·미동의 시 null 이다."*). 우리는 그 컬럼의 존재를 인지하지
않은 채 `(member_id, provider, provider_user_id)` 만 넣었고, NULL 이 정상값인
컬럼이라 아무 오류가 없었다. 사고는 그로부터 며칠 뒤 back 이 `V6` 로 `SET NOT NULL`
을 걸 때 났다.

그러므로 방어의 대상은 "NOT NULL 제약" 이 아니라 "우리가 값을 주지 않는 컬럼" 이다.
그 컬럼이 언젠가 NOT NULL 이 될 후보다.

### 1.4 결함 3 — worktree 에는 `.env` 도 `.demo/` 도 없다

`_client.py` 의 `KEY_PATH` 는 `ROOT/.demo/demo-jwt-key.pem` 이고 `ROOT` 는 `_client.py` 위치
기준이다. worktree 에서는 그 경로가 worktree 안을 가리키고, `.demo/` 는 gitignore 라
존재하지 않는다. 그래서 `ensure_key()` 가 새 키를 만든다. back 에 주입된 것은 메인
워킹트리의 키이므로 전 요청이 401 이 된다.

401 이 어디서 드러나는가도 확인했다. `BackClient._csrf_token()` 이 CSRF 쿠키를 못
받아 `RuntimeError` 를 내고, 메시지에 원인 추정까지 적혀 있다. 문제는 그 시점이다.
`main()` 은 `--reset` 을 먼저 돌리므로 기존 데이터를 전부 지운 뒤에 인증이 실패한다.
`T28`(UnicodeEncodeError 로 reset 직후 실행 중단)과 구조가 같다.

---

## 2. 무엇을 만들었나

`tools/demo_seed/preflight.py` 하나가 세 결함의 방어를 모은다. `seed.py`가
**`--reset`보다 먼저** 부르고, 걸리면 아무것도 지우지 않은 채 종료 코드 2로 끝난다.

| # | 검사 | 결함 | 판정 |
|---|---|---|---|
| 1 | 접속 DB를 첫 줄에 찍는다(비밀번호 마스킹) | §1.1 | 정보 |
| 2 | 쓰기 컬럼 계약 대조 | 1 | 차단 |
| 3 | 미적용 back 마이그레이션 | 1 | 우리 테이블을 건드리면 차단 |
| 4 | JWT 키 실검증 | 3 | 차단 |
| 5 | 고아 `ai.*` 행 집계 | 2 | 경고 |

| 파일 | 무엇을 · 왜 |
|---|---|
| `tools/demo_seed/preflight.py` (신규) | 위 다섯. 판정 3종(`diff_write_contract`·`pending_migrations`·`format_orphans`)은 순수 함수로 분리했다. 일부러 어긋내 실패를 확인하는 것이 이 코드의 유일한 검증 수단이라, 그 테스트가 DB·HTTP·`.env` 없이 돌아야 하기 때문이다 |
| `tools/demo_seed/_client.py` | `shared_root()` 신설 — `--git-common-dir`로 worktree 안에서도 메인 워킹트리를 가리킨다. `KEY_PATH`가 이것을 쓴다. `ensure_key()`는 키를 새로 만들 때 stderr로 알린다(아무 표시 없이 만드는 것이 결함 3의 절반이었다) |
| `tools/demo_seed/seed.py` | preflight를 `--reset` 앞에 배치. `reset()`의 `ai.*` 삭제를 `ORPHAN_TABLES` 순회로 바꿔 목록을 단일화. `--prune-orphans` 신설. 시딩 종료 후에도 고아를 센다 |
| `tests/test_demo_seed_preflight.py` (신규) | 판정 3종 15케이스. 대부분이 "걸려야 하는 입력" |
| `tools/demo_seed/README.md` | preflight 절·`--prune-orphans`·키 경로 고정 |

### 결함 1 — 방어 대상은 제약이 아니라 "우리가 값을 주지 않는 컬럼"이다

`WRITE_CONTRACT`가 시딩이 직접 INSERT하는 두 테이블의 모든 컬럼을 셋 중 하나로
선언한다(`SEED` 우리가 채운다 / `DB` 기본값·IDENTITY에 맡긴다 / `NULL` 의도적으로
비운다). 선언에 없는 컬럼이 실제 스키마에 나타나면 거기서 멈춘다.

```
core.social_account: 계약에 없는 컬럼 `nickname` (nullable=True, default=False)
  — back이 컬럼을 추가했다. 시딩이 값을 넣을지 정하고 WRITE_CONTRACT에 선언하라.
    NULL로 두면 back이 나중에 NOT NULL을 걸 때 기동이 실패한다(V6 전례)
```

nullable인 새 컬럼에서 걸리는 것이 핵심이다. 제약이 걸리는 시점에는 이미 늦고,
컬럼이 생기는 시점에는 아직 아무 오류도 나지 않는다. 그 사이가 유일한 기회다.

### 결함 2 — 목록을 두 벌 두지 않는다

`reset()`이 지울 테이블과 고아를 셀 테이블이 같은 상수(`ORPHAN_TABLES`)다.
목록에서 빠진 테이블은 reset이 안 지우면서 집계도 세지 않으므로 아무 표시 없이
쌓인다. 그것이 `context_keyword_analysis`가 222행이 된 경로다. 하나로 묶으면 새 테이블을
빠뜨려도 집계가 먼저 그것을 고아로 보고한다.

### 결함 3 — 묶는 것과 드러내는 것을 둘 다 한다

`shared_root()`가 키 경로를 하나로 묶고, 인증 프로브가 back이 그 키를 실제로
받아들이는지 확인한다. 둘은 다른 일을 한다. 경로를 묶어도 back에 주입된
`JWT_PRIVATE_KEY`가 다른 키면 여전히 401이며, 그것은 파일 배치로 막을 수 없다.

프로브는 `provider_user_id='__preflight__'` member 한 쌍을 만들어 인증된 GET을
한 번 던지고 `finally`에서 지운다.

## 3. 논의 포인트에 대한 판단

### 3.1 결함 1의 방어를 어디에 두는가 — 후보 셋 중 둘, 형태를 바꿔서

「시딩 후 back 기동 스모크」는 넣지 않았다. back 기동은 우리 도구의 책임 밖이고
느리며, 무엇보다 시점이 늦다. 그 시점엔 DB가 이미 오염돼 있어 관측은 되지만 예방은
안 된다. 실제로 이 세션에서 back을 띄워 보니 낡은 jar 때문에 Flyway가 기동을
거부했는데(§4.3), 그 진단은 우리가 만든 어떤 장치도 아닌 back의 스택트레이스가 했다.

「컬럼 집합 대조」는 넣되 방향을 바꿨다. "제약을 감시한다"가 아니라 "우리가 값을
주지 않는 컬럼을 선언하게 만든다"이다. 전자는 back이 언제 무엇을 걸지 알아야
하지만, 후자는 우리 쪽 인지 상태만 관리하면 된다. back이 우리 CI가 아니라는
현실에서 실제로 작동하는 것은 이쪽이다.

「트랜잭션 경계」는 다루지 않았다. 결함 1은 트랜잭션 문제가 아니다. INSERT는 정상
커밋됐고 며칠 뒤 다른 프로세스가 실패했다. 경계를 조여도 이 사고는 그대로 난다.

대신 「미적용 마이그레이션 감지」를 추가했다. back 레포가 로컬에 있으면
`flyway_schema_history`와 대조해 우리 테이블을 건드리는 미적용분을 차단한다.
back 레포가 없으면(CI·배포) 검사 없이 건너뛴다. 계약이 아니라 로컬 편의이기
때문이다.

### 3.2 `--reset`이 무엇까지 지워야 하는가 — 경고만 하고 남긴다

지우지 않는다. 근거가 취향이 아니라 사실에 있다.

`tools/e2e/`의 검증 데이터는 의도적으로 `core`에 대응 행이 없는 `ai` 단독
데이터다(`README.md` "주의"가 명시한다). 그것이 고아 정의에 전부 걸린다. 자동
삭제는 다른 하네스를 깨뜨린다. 동시에 그 행들이 집계를 틀리게 만드는 것도
사실이다(`-191`의 저장 비용 8행). 둘 다 참이므로 도구가 고를 문제가 아니다. 세어서
보여주고 `--prune-orphans`로 사람이 정한다. 삭제는 되돌릴 수 없고 보고는 되돌릴 수
있다.

단, `demo-seed` member 소유 context의 `ai.context_keyword_analysis` 행을 reset이
지우지 않던 것은 논쟁거리가 아니라 누락이라 그냥 고쳤다(§2 결함 2).

### 3.3 결함 3을 고칠 것인가 드러낼 것인가 — 둘 다, 그러나 드러내기가 본질

키 경로를 묶는 것은 한 가지 갈라짐(worktree)만 막는다. back에 주입된 키가 다른
경우·`PINLOG_DEMO_JWT_KEY`를 잘못 준 경우는 그대로 남는다. 인증 프로브는 원인과
무관하게 결과를 잡으므로 이쪽이 방어의 본체다. 경로 고정은 가장 흔한 원인 하나를
없애는 보조다.

### 3.4 셋 중 빼는 것이 나은 것 — 없다. 다만 하나가 늘었다

셋 다 같은 도구의 같은 진입점에 모이므로 나누는 것이 오히려 비싸다. 대신 §1.1의
접속 DB 갈라짐을 발견해 첫 줄 출력으로 넣었다. `.env` 기본값을 고치는 것은
`ai` 소유이지만 `-174` 절차 문서·다른 세션의 습관과 얽혀 있어 이 티켓에서 바꾸지
않았다(§5).

## 4. 검증 — 방어가 실제로 걸리는가

통과만 확인하면 아무것도 검사하지 않는 장치를 놓친다. 그래서 셋 다 일부러 어긋내
실패를 확인한 뒤 되돌렸다(`S15P11A705-156`이 완료 조건에 넣은 것과 같은 이유다).

### 4.1 판정 로직 — 뮤턴트로 확인

`tests/test_demo_seed_preflight.py` 15케이스 통과. 그다음 `diff_write_contract`
첫 줄에 `return []`를 넣어 판정을 무력화했더니 5건이 실패했다.

```
FAILED test_back이_컬럼을_추가하면_걸린다
FAILED test_사고_당시_계약이라면_email에서_걸린다
FAILED test_컬럼이_사라지면_걸린다
FAILED test_우리가_안_채우는_컬럼에_NOT_NULL이_걸리면_잡힌다[db]
FAILED test_우리가_안_채우는_컬럼에_NOT_NULL이_걸리면_잡힌다[null]
```

되돌린 뒤 15건 재통과. 테스트가 판정을 실제로 검사한다.

### 4.2 실제 스키마 대조 — 프로브 DB에서 사고를 재현

`:15432` 서버에 `guardprobe` DB를 새로 만들고 back 마이그레이션 9개를 전부
적용한 뒤, `check_write_contract`를 실제 `information_schema`에 대해 돌렸다.
기존 데이터를 건드리지 않기 위해 별도 DB를 썼고 끝난 뒤 DROP했다.

| 단계 | 스키마 상태 | 결과 |
|---|---|---|
| 1 | V6까지 적용된 현재 그대로 | 문제 없음 |
| 2 | `ADD COLUMN nickname VARCHAR(50)` (nullable) | **RED** — 계약에 없는 컬럼 |
| 3 | 그 컬럼에 `SET NOT NULL` | **RED** |
| 4 | `DROP COLUMN nickname` | 문제 없음 |

2단계가 이 티켓의 요점이다. 컬럼이 nullable로 추가된 시점, 즉 `email`이 V4에서
추가됐고 우리가 놓쳤던 그 순간에 걸린다.

> 이 실행이 오탐 하나를 잡았다. 처음에는 `id`가 "NOT NULL인데 기본값이 없다"로
> 걸렸다. `GENERATED ALWAYS AS IDENTITY`는 `information_schema.columns`에서
> `column_default`가 NULL이고 `is_identity`가 별도 컬럼이기 때문이다. 단위 테스트는
> 이 쿼리를 타지 않아 잡히지 않았다. `is_identity`·`is_generated`를 포함하도록 고쳤다.

### 4.3 실행 — 네 가지 환경에서

`.env`를 주입하고 worktree 코드로 `preflight.py`를 직접 돌렸다.

| # | 환경 | 결과 | 종료 |
|---|---|---|---|
| C | `:5433` (`core` 테이블 없음 — `.env` 기본값이 가리키는 DB) | `[BLOCK]` 테이블 없음 ×2 + 미적용 마이그레이션 9개 | **2** |
| D | `:15432` · back 내려간 상태 | 계약 `[ok]` → `[BLOCK]` 인증 프로브 `ConnectError` | **2** |
| E | `:15432` · back 기동 · 올바른 키 | 계약 `[ok]` · 인증 `[ok]` · `[WARN]` 고아 222 | **0** |
| F | `:15432` · back 기동 · **다른 키** | `[BLOCK]` `HTTP 401 UNAUTHORIZED`, 쓰고 있는 키 경로까지 출력 | **2** |

F가 결함 3의 재현이다. 셋 다 `--reset` 이전에 멈췄고 로그가 "아무것도 지우지
않았다"로 끝난다. C가 §1.1(다른 DB에 표시 없이 붙는 것)도 같이 잡는다.

back은 §7 절차로 실제 기동했다. 첫 기동은 실패했다. DB에는 V6가 적용돼 있는데
로컬 `build/libs/` jar이 07-30 빌드라 V6를 담고 있지 않아 Flyway가
`Detected applied migration not resolved locally: 6`으로 거부했다. `bootJar`
리빌드 후 정상 기동(`Current version of schema "public": 102`).

### 4.4 키 경로 고정

worktree 안에서 `_client`를 import해 확인했다.

```
ROOT         ...\ai\.claude\worktrees\seed-guard      ← 코드는 worktree
shared_root  ...\ai                                    ← 키는 메인 워킹트리
KEY_PATH     ...\ai\.demo\demo-jwt-key.pem   존재: True
```

고침 전이라면 `ROOT/.demo/`를 가리켰고 그 디렉터리는 worktree에 없다(gitignore).
즉 `ensure_key()`가 새 키를 만들었을 지점이다.

### 4.5 삭제 범위 — 트랜잭션 롤백으로 확인

실데이터를 지우지 않기 위해 트랜잭션 안에서 실행하고 롤백했다.

```
[H] reset 의 ai.* 삭제 (demo-seed context 37건 소유분)
    ai.context_ai_state           DELETE 37
    ai.context_embedding          DELETE 37
    ai.context_keyword            DELETE 72
    ai.context_keyword_analysis   DELETE 37     ← 고치기 전에는 0이었다
[I] --prune-orphans
    {..., 'ai.context_keyword_analysis': 222}   ← 고아만. 나머지 셋은 0
```

인증 프로브가 남긴 행도 확인했다 — `core.member` 7명 그대로,
`provider_user_id='__preflight__'` 0건.

### 4.6 회귀

```
pytest (worktree 전체)   254 passed      exit 0
ruff check .             All checks passed
```

`tools/`는 ruff `extend-exclude` 대상이라 게이트에 들지 않는다. 그래도 확인했고,
남은 `E501`·`I001`은 `dev` 시점과 동수다(내가 늘리지 않았다).

## 5. 남은 문제

- **`.env`의 `DATABASE_URL`이 `:5433`을 가리킨다**(§1.1). 시연 정본은 `:15432`이고
  `-174` §7 절차가 매 명령에 덮어쓴다. 기본값을 바꾸는 것은 `ai` 소유 파일이지만
  절차 문서·다른 세션의 습관과 얽혀 있어 이 티켓에서 손대지 않았다. preflight가
  접속 대상을 첫 줄에 찍으므로 틀린 DB에 붙으면 즉시 보인다.
- **`back`의 `build/libs/` jar이 낡아 있었다**(§4.3). 검증을 위해 `bootJar`로
  리빌드했다. 소스는 건드리지 않았고 산출물만 갱신됐다.
- **`verify.py`에는 preflight를 붙이지 않았다.** 그쪽은 읽기만 하므로 실패해도
  잃을 것이 없고, 인증이 깨지면 검증이 FAIL로 뜬다.
- **고아 222행은 그대로 남겼다**(§3.2). 지울지는 사람이 정한다.

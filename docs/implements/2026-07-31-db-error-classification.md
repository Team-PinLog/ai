# DB 실패의 오류 분류 — 검색 중 DB 접속이 실패하면 500 대신 503 을 반환하게 했다

- **티켓**: S15P11A705-221
- **상태**: 완료
- **날짜**: 2026-07-31
- **선행**: [검색 API 오류 응답 계약](2026-07-31-search-error-contract.md) (`-220`) ·
  [외부 API 재시도·오류 분류](2026-07-30-retry-and-error-classification.md) (`-121`)
- **명세**: [`failure-recovery.md`](../spec/failure-recovery.md) §2.5 「DB 축」

## 이 문서가 다루는 것

`-220` 이 남긴 항목 하나를 처리했다. 그 티켓은 `TransientError→503` ·
`PermanentError→502` 핸들러를 넣으면서 500 을 「우리 코드의 결함」 전용으로 비워 두는
설계를 택했다. 그런데 DB 실패는 어느 쪽으로도 분류되지 않아 그대로 500 으로 나가고
있었다.

```
검색 중 DB 접속 실패 → 분류 없음 → uvicorn → HTTP 500
```

이 상태에서는 커넥션 풀 고갈이나 DB 재기동처럼 기다리면 회복되는 상황이 「코드가
깨졌다」와 같은 상태 코드를 쓴다. `-220` 이 500 에 부여한 의미가 그만큼 다시 흐려진다.

명세는 이미 분류를 요구하고 있었다. `failure-recovery.md` §2.1 이 *"DB 연결 실패,
잠금 타임아웃, 직렬화 실패"* 를 일시적 오류로 규정해 두었는데, 코드가 그 표를 따르지
않고 있었다. 따라서 이 티켓은 새 정책을 만든 것이 아니라 명세와 코드의 간극을 메운
것이다.

---

## 1. 조사 — asyncpg 는 실제로 무엇을 던지는가

짐작하지 않고 실측했다(asyncpg 0.31.0, Python 3.12). 가장 중요한 발견은 세 가지다.

### 1.1 접속 실패는 asyncpg 예외가 아니다

이 티켓의 제목이 「DB 연결 실패」인데, 정작 그 실패는 `asyncpg.*` 타입으로 오지
않는다.

| 상황 | 실제 타입 | 계층 |
|---|---|---|
| 포트가 닫혀 있다 | `ConnectionRefusedError` | stdlib `OSError` |
| DNS 실패 | `socket.gaierror` | stdlib `OSError` |
| 접속 타임아웃 | `TimeoutError` | stdlib `OSError` |
| **풀 획득 타임아웃** | `TimeoutError` | stdlib `OSError` |

`asyncpg` 예외만 분류했다면 이 티켓이 다루려는 실패의 본체를 통째로 놓쳤을 것이다.
티켓 본문의 *"repository 층에서 asyncpg 예외를 분류"* 라는 문구 그대로 구현했다면
접속 실패는 여전히 500 으로 나갔을 것이고, 그런데도 테스트는 통과했을 것이다.

풀 획득 타임아웃이 `OSError` 로 잡히는 것은 Python 버전에 따라 달라지는 사실 하나에
걸려 있다. Python 3.11 부터 `asyncio.TimeoutError` 가 내장 `TimeoutError` 와 같은
객체이고, 그 타입이 `OSError` 의 하위 타입이다. 버전이 바뀌면 조용히 깨질 수 있는
전제이므로 테스트로 고정했다
(`test_pool_acquire_timeout_is_an_os_error_on_this_python`).

### 1.2 `InterfaceError` 하나가 성격이 정반대인 두 실패를 함께 쓴다

| 메시지 | 성격 |
|---|---|
| `connection is closed` / `pool is closed` | 커넥션 수명주기 문제 |
| `the server expects 2 arguments, 1 was passed` | 우리 코드의 결함 |

두 실패가 같은 예외 타입으로 온다. 이 타입을 일시 오류로 분류하면 인자 개수 오류
같은 코드 결함이 503 뒤에 영구히 숨는다. 사용자에게는 「일시적으로 사용할 수
없습니다」가 뜨고, 재시도해도 실패가 반복되고, 코드 결함인데도 알림은 울리지
않는다. 티켓이 경고한 바로 그 실패 모드다.

### 1.3 DB 가 중단되는 순간의 질의는 `08003` 이다

백엔드 프로세스를 실제로 종료해 보면 질의 중이던 커넥션은
`ConnectionDoesNotExistError`(`08003`, *connection was closed in the middle of
operation*)를 받는다. 같은 커넥션에 한 번 더 질의하면 그때는
`InterfaceError('connection is closed')` 다. 즉 §1.2 의 수명주기 쪽 메시지는 정상
경로에서 나오지 않는다. 만약 나온다면 우리 코드가 닫힌 커넥션을 재사용하고 있다는
뜻이다.

---

## 2. 결정

### 2.1 경계 — 어디까지가 일시적인가

이 티켓의 핵심 산출물이다. 기준을 한 줄로 그었다.

> 서버나 연결의 상태 때문에 실패했으면 일시적이고, 우리가 보낸 질의 때문에
> 실패했으면 우리 결함이다.

SQLSTATE 군 단위로 경계를 긋되, 군 안에서 성격이 갈리는 세 군(`25xxx`·`55xxx`·
`42xxx`)만 개별 코드 단위로 지정했다. 전체 표는
[`failure-recovery.md` §2.5 「DB 축」](../spec/failure-recovery.md)과
[`app/core/db_errors.py`](../../app/core/db_errors.py) 모듈 도크에 있다.

```
Transient(503)  OSError(접속 단계) · 08xxx · 40xxx · 53xxx · 57xxx · 58xxx · 55P03 · 25P03 · 25P04
Permanent(502)  28xxx 인증 · 3D000 없는 DB · 42501 권한
미분류(500)      42xxx 나머지 · 22xxx · 23xxx · XX000 · InterfaceError
```

미분류 목록이 분류 목록만큼 중요하다. 일시 오류의 범위를 넓게 잡으면 코드 결함이
503 뒤에 숨는다. 그것이 이 경계를 그을 때 가장 경계한 실패 모드다.

### 2.2 `OSError` 는 접속 단계에서만 번역한다

`OSError` 는 DB 와 무관한 코드도 던지는 광범위한 타입이다. 그래서 커넥션을 얻는
동안에만 DB 오류로 번역하고, 커넥션을 넘겨준 뒤의 블록(호출부 코드가 함께
들어온다)에서는 `asyncpg` 예외만 번역한다.

이 구분이 없으면 DB 와 무관한 파일·소켓 오류까지 「DB 가 일시적으로 안 된다」로
잘못 분류된다.

### 2.3 분류를 붙이는 지점은 세션 경계다 (티켓 문구와 다르다)

티켓은 *"repository 층에서"* 라고 적었지만, 실제로는 `app/core/db.py` 의
`acquire()`/`transaction()` 에 넣었다. 이유는 §1.1 에 있다. repository 함수에
데코레이터를 걸면 `pool.acquire()` 호출이 데코레이터 바깥에 있어 접속 실패를
놓친다. 세션 경계에 두면 커넥션 획득과 질의가 한 번에 덮이고, repository 를 새로
만들어도 분류가 자동으로 따라온다. 세션 경계는 repository 보다 아래 층이므로,
*"service 위로는 분류된 예외만 올라간다"* 는 티켓의 취지는 그대로 유지된다.

### 2.4 `-220` 의 핸들러를 고치지 않는다 — 대신 하위 타입을 쓴다

`DatabaseTransientError(TransientError)` · `DatabasePermanentError(PermanentError)`
로 정의했다. 기존 핸들러가 `isinstance` 검사로 하위 타입을 그대로 받으므로
`main.py` 는 한 줄도 바뀌지 않았다. 그러면서 핸들러 로그의 `type(exc).__name__` 이
실패한 하위 시스템이 게이트웨이가 아니라 DB 라는 것을 알려 준다.

```
WARNING app.main  upstream transient failure: DatabaseTransientError
```

### 2.5 예외 메시지에 타입 이름과 SQLSTATE 만 담는다

DSN 에는 DB 비밀번호가 들어 있고, 접속 실패 예외 메시지에는 host·port 가 섞일 수
있다. `-220` 이 게이트웨이 응답 200자 누출을 발견하고 세운 기준을 그대로 적용했다.

```python
DatabaseTransientError("TooManyConnectionsError[53300]")   # 원본 메시지는 옮기지 않는다
```

원본 예외는 `__cause__` 로만 매달아 둔다. 대신 SQLSTATE 는 메시지에 남긴다.
SQLSTATE 는 공개 어휘이면서 *"풀이 고갈됐다"* 와 *"DB 가 재기동 중이다"* 를 구분하는
유일한 값인데, 핸들러 로그는 타입 이름만 찍으므로 이 값이 남을 곳이 예외 메시지뿐이다.
분류가 붙는 순간 `app.core.db` 로거가 한 줄 남긴다(일시 오류는 `WARNING`, 영구
오류는 `ERROR` — §2.4).

### 2.6 재시도를 넣지 않는다

티켓이 확정한 대로다. `-121` 이 정한 짧은 재시도는 GMS 호출 대상이고, DB 재시도는
커넥션 풀 동작과 얽혀 있어 별개 문제다. 지금 구조에서 DB 일시 오류는 `503` 으로
나가고 `back` 은 검색을 재시도하지 않으므로(`AiSearchClient`), 사용자 화면은 이 티켓
전후로 같다. 바뀌는 것은 운영자가 보는 관측 정보이고, 이는 `-220` 과 같은 성격의
개선이다.

---

## 3. 검증

### 3.1 계약 테스트 — `tests/test_api_error_contract.py` DB 절 8건

`-220` 이 세운 원칙을 그대로 지켰다. DB 도 Fake 로 바꾸지 않는다. 예외를 만들어
주입하면 분류 경로(`db.acquire()` → `db_errors.py`)를 건너뛰게 되고, 그러면 분류
경계가 바뀌어도 테스트가 계속 통과한다. `ai#69` 결함을 테스트가 놓친 것과 같은
구조의 구멍이다.

대신 실제 `asyncpg` 풀을 실제로 실패시켰다.

| 무엇을 | 어떻게 실패시켰나 | 단언 |
|---|---|---|
| DB 에 닿지 않는다 | 닫힌 포트를 가리키는 `min_size=0` 풀 | `503`, 임베딩은 1회 성공 |
| 질의 도중 끊긴다 | 서버가 실제로 백엔드를 종료한다(`08003`) | `503` |
| 서버가 질의를 취소한다 | `statement_timeout`(`57014`) | `503` |
| 없는 데이터베이스를 가리킨다 | 실제 서버가 `3D000` 으로 거절 | `502` |
| 없는 컬럼을 참조한다 | 실제 서버가 `42703` | **`500` 유지** |
| 드라이버 오사용 | 인자 개수 부족(`InterfaceError`) | **`500` 유지** |
| 오류 응답 본문 | 접속 불가 | DSN·비밀번호·host:port **없음** |
| 정상 DB | — | `200` |

`min_size=0` 풀이 이 테스트를 성립시키는 장치다. 운영 `Database.connect()` 는
`min_size=1` 이라 접속이 깨지면 기동 자체가 실패한다. 그것은 lifespan 의 문제이지
요청 경로의 문제가 아니다. `min_size=0` 은 접속 시점을 첫 `acquire()` 로 미루므로,
서버가 떠 있는 동안 DB 가 닿지 않게 되는 순간(풀 재충전 실패·DB 재기동)을 요청
경로 안으로 옮길 수 있다. 풀·드라이버·예외는 전부 실물이다.

### 3.2 분류표 단위 테스트 — `tests/test_db_error_classification.py` 40건

관통 테스트는 실제 DB 로 만들 수 있는 실패만 확인할 수 있어서, 분류표의
대부분(`40xxx` 직렬화, `53xxx` 자원, `28xxx` 인증 등)을 덮지 못한다. 그래서 표
자체를 별도 단위 테스트로 고정했다. 이 테스트는 DB 를 띄우지 않는다.
`classify_db_error` 를 예외를 던지는 함수가 아니라 분류 결과를 반환하는 함수로
만든 이유가 이것이다(`classify_http_status` 와 같은 판단).

### 3.3 구현 전 실패 확인 → 구현 후 통과

`app/core/db.py` 를 `origin/dev` 판으로 되돌려 신규 테스트 4건이 실패하는 것을
먼저 확인했다. 실패 형태는 「500 이 나왔다」가 아니라 `ConnectionRefusedError` 가
응답 계층까지 그대로 새어 나가는 것이었고, 그것이 운영에서 uvicorn 이 500 을 만드는
바로 그 지점이다.

구현 후 전체 356건 통과, coverage line 99.82% / branch 98.84% (게이트 80/80).

### 3.4 실서버 대조 — DB 를 실제로 멈춰 봤다

테스트만으로는 「분류 코드를 넣었다」까지만 확인된다. 실제 동작을 보기 위해 전용
pgvector 컨테이너(스키마 + preset 27건)와 임베딩 스텁에 uvicorn 두 대를 물려
대조했다. 시연 DB 를 건드리지 않도록 격리된 컨테이너를 따로 띄웠다.

| 상황 | `origin/dev`(43d9c02) | 이 브랜치 |
|---|---|---|
| DB 정상 | `200` `{"results":[]}` | `200` `{"results":[]}` |
| **DB 중단** | **`500`** `Internal Server Error` + 트레이스백 | **`503`** `{"detail":"embedding upstream unavailable"}` |
| DB 중단, `/ready` | — | `503` `{"status":"not_ready"}` (기존대로) |

이 브랜치의 로그는 다음과 같다.

```
WARNING app.core.db  db transient failure: ConnectionRefusedError
WARNING app.main     upstream transient failure: DatabaseTransientError
INFO:    "POST /internal/v1/search HTTP/1.1" 503 Service Unavailable
```

두 줄이 각각 원인(`ConnectionRefusedError`)과 실패한 하위
시스템(`Database...`)을 알려 준다. 대조군인 `origin/dev` 는 트레이스백만 남았다.

---

## 4. 남은 문제

- **응답 본문이 여전히 `embedding upstream ...` 이다.** DB 에서 비롯한 `503`·`502`
  응답도 임베딩을 가리키는 문구를 답한다. 상태 코드와 로그는 정확하고, `back` 은 이
  본문을 읽지 않으므로(`AiSearchClient` 는 `serverProfile` 을 보는 422 외에는 본문을
  버린다) 이번 범위에서 고치지 않았다. 본문을 하위 시스템 중립 문구로 바꾸는 것은
  `-220` 이 정한 계약의 개정에 해당하고, 티켓이 *"핸들러를 바꾸지 않는다"* 로
  확정했다. 후속 티켓 후보다.
- **`53100` 디스크 가득 참을 일시 오류로 두었다.** 재시도로 낫지 않는 상황이므로
  논쟁의 여지가 있다. 그러나 우리 질의의 결함이 아니고, 현재 분류 체계에 세 번째
  칸이 없다. `500` 으로 두면 「우리 코드가 깨졌다」를 잘못 가리키게 된다. 분류표에서
  가장 근거가 약한 칸이며, 명세에도 그렇게 적었다.
- **Context 처리 경로(`/context/process`)의 동작은 바뀌지 않는다.** 202 를 먼저
  반환하고 백그라운드로 도는 구조라 예외가 응답이 되지 않는다. 다만 그 경로의 DB
  실패도 이제 `TransientError` 로 분류되어 올라오므로, §2.1 의 *"상태를 PROCESSING
  으로 둔 채 종료"* 를 나중에 그 경로에 붙일 때 분류가 이미 준비되어 있다. 두
  서비스의 `except TransientError` 블록은 클라이언트 호출만 감싸고 있어 이번 변경의
  영향을 받지 않는다(확인함).
- **DB 재시도를 넣지 않았다**(§2.6). 커넥션 풀 동작과 얽혀 있어 별개 문제다.
- **Circuit breaker 는 여전히 미구현이다.** `failure-recovery.md` §2.2 가 언급하지만
  이 티켓이 다루는 문제가 아니다.

## 관련 장애 기록

경계를 긋는 동안 발견한 문제 세 건을
[2026-07-31-db-error-pitfalls.md](../troubleshooting/2026-07-31-db-error-pitfalls.md)
(T53~T55)에 남겼다.

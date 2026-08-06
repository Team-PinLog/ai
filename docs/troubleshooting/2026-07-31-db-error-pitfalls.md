# DB 오류 분류의 경계를 그으며 만난 함정 (2026-07-31)

`S15P11A705-221` — DB 실패를 `TransientError`/`PermanentError` 로 분류하고, 그것을
**로컬에서 실제로 DB 를 멈춰** 확인하는 과정에서 만난 세 문제다.

셋 다 **증상이 원인을 가리키지 않는다.** 첫째는 테스트가 전부 초록인데 티켓이 해결되지
않는다. 둘째는 재현 코드가 재현하려던 지점에 닿기 전에 실패한다. 셋째는 셸 문제가 앱의 결함처럼
보인다.

관련 구현 기록: [db-error-classification](../implements/2026-07-31-db-error-classification.md)

---

## T53. 「DB 접속 실패」는 `asyncpg` 예외가 **아니라서**, asyncpg 만 보고 분류표를 짜면 티켓 본체를 놓친다

티켓 본문은 "repository 층에서 asyncpg 예외를 `TransientError`/`PermanentError` 로 분류"
라고 적었다. 그대로 하면 이렇게 된다.

```python
except asyncpg.PostgresConnectionError:   # 08xxx — 연결 예외
    raise DatabaseTransientError(...)
```

**그런데 이렇게 해도 DB 접속 실패는 여전히 500 으로 나간다.** 실측하면 이렇다(asyncpg 0.31.0).

| 상황 | 실제 타입 | 계층 |
|---|---|---|
| 포트가 닫혀 있다 | `ConnectionRefusedError` | stdlib `OSError` |
| DNS 실패 | `socket.gaierror` | stdlib `OSError` |
| 접속 타임아웃 | `TimeoutError` | stdlib `OSError` |
| **커넥션 풀 획득 타임아웃** | `TimeoutError` | stdlib `OSError` |

`08xxx` `PostgresConnectionError` 는 **이미 붙어 있던 연결이 끊길 때** 서버가 보내는
SQLSTATE 다. 애초에 붙지 못하는 실패에서는 서버가 아무 응답도 보내지 않으므로 SQLSTATE 가 없고,
파이썬 소켓 계층의 예외가 그대로 올라온다.

**이 함정이 위험한 이유는 테스트가 초록이기 때문이다.** 분류표를 검증하는 테스트는
자연히 `asyncpg.*` 타입으로 짜게 되고, 그 전부가 통과한다. 티켓의 완료 조건인
"DB 연결 실패가 503 으로 나간다"만 드러나지 않은 채 거짓으로 남는다.

두 가지 원칙이 따라온다.

1. **`OSError` 를 접속 단계에서만 번역한다.** 커넥션을 넘겨준 뒤의 블록에는 호출부 코드가
   함께 들어오고, 거기서 나온 `OSError` 는 DB 의 것이 아닐 수 있다.
2. **분류를 repository 함수가 아니라 세션 경계에 건다.** repository 함수에 데코레이터를
   걸면 `pool.acquire()` 가 그 밖에 있어, 접속 실패가 애초에 데코레이터 안으로 들어오지 않는다.

풀 획득 타임아웃이 `OSError` 로 잡히는 것은 **Python 버전 사실 하나**에 걸려 있다. 3.11
부터 `asyncio.TimeoutError` 가 내장 `TimeoutError` 와 같은 객체이고, 그것이 `OSError` 의
하위 타입이다. 버전이 바뀌면 알아차리지 못한 채 깨질 수 있으므로 테스트로 못박아 두었다.

## T54. `min_size=1` 풀에서는 「요청 중 DB 불가」를 재현할 수 없다 — 요청 전에 기동이 먼저 실패한다

닿지 않는 주소로 `Database` 를 만들어 요청을 넣어 보려 했더니, 요청은커녕 픽스처
단계에서 실패했다.

```python
db = Database("postgresql://u:p@127.0.0.1:1/pinlog")
await db.connect()          # ← 여기서 ConnectionRefusedError
```

`asyncpg.create_pool` 이 `min_size` 만큼 **즉시** 커넥션을 연다. 운영 `Database.connect()`
는 `min_size=1` 이므로 접속이 깨지면 lifespan 이 실패한다. 그것은 **기동 실패**이지 이
티켓이 겨냥한 「요청 처리 중 DB 불가」 경로가 아니다.

`connect()` 를 건너뛰고 `_pool` 을 비워 두는 것도 답이 아니다. 그러면
`RuntimeError("Database pool not initialized")` 가 난다. 그것은 우리 쪽 초기화 결함이라 **500 이 맞는
응답**이므로, 재현하려던 것과 다른 것을 측정하게 된다.

`min_size=0` 이 접속 시점을 첫 `acquire()` 로 미룬다.

```python
class _LazyPoolDatabase(Database):
    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=0, max_size=1)
```

이 장치가 *서버가 떠 있는 동안 DB 가 닿지 않게 되는* 순간(풀 재충전 실패·DB 재기동)을 요청
안으로 옮긴다. **풀·드라이버·예외는 전부 실물이다.** 예외 객체를 만들어 주입하는 방식과는 검증하는
대상이 다르다.

> 실서버 대조에서는 이 장치가 필요 없다. `docker stop` 으로 DB 를 멈추면 운영 풀도 재충전을
> 시도하다 같은 `ConnectionRefusedError` 를 낸다(실측). 장치가 필요한 것은 **테스트가
> 기동을 통과해야 하기 때문**이다.

## T55. Git Bash `curl` 로 한글 본문을 보내면 400 이 난다 — 앱의 스키마 결함처럼 보인다

실서버 대조 중 첫 검색 요청이 이렇게 돌아왔다.

```
{"detail":"There was an error parsing the body"}
HTTP 400
```

`SearchRequest` 스키마를 의심하게 되는 응답이다. 실제 원인은 셸이 본문을 UTF-8 로 보내지
않은 것이다.

```bash
curl -d '{"query":"카페", ...}'    # 400 — 본문 바이트가 깨진다
curl -d '{"query":"cafe",  ...}'    # 200 — 같은 엔드포인트·같은 헤더
```

질의어만 ASCII 로 바꾼 같은 요청이 통과하면 **엔드포인트가 아니라 본문 바이트**가 원인이다.
확실히 하려면 본문을 UTF-8 파일에 써서 `--data-binary @file` 로 넘긴다.

이 티켓처럼 **한글이 검증 대상이 아닌** 대조에서는 ASCII 질의로 충분하다. 검색 품질을
보는 작업이라면 파일 경로를 써야 한다. 그런 작업에서는 질의어 자체가 측정 대상이기 때문이다.

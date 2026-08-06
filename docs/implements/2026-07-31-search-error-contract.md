# 검색 API 오류 응답 계약 — 임베딩 502 가 검색 500 이 되던 것을 끊는다

- **티켓**: S15P11A705-220
- **상태**: 완료
- **날짜**: 2026-07-31
- **선행**: [외부 API 재시도·오류 분류](2026-07-30-retry-and-error-classification.md) (`-121`) ·
  [GMS 호출 계측](2026-07-31-gms-call-observability.md) (`-197`)
- **근거**: [`ai#69`](https://github.com/Team-PinLog/ai/issues/69) — 인프라 파트 운영 보고

## 이 문서가 다루는 것

운영에서 실제로 나던 버그다. 임베딩 게이트웨이가 `502` 를 주면 `/internal/v1/search` 가
`500` 으로 실패했다.

```
임베딩 502 → 3회 재시도 → TransientError → API 응답 500
```

분류도 재시도도 이미 맞았다. `-121` 이 `classify_http_status` 로
`502 → TransientError` 를 확정했고 `retry.py` 가 지수 백오프로 세 번 시도한다.
`tests/test_client_retry.py` 가 그 매핑을 상태 코드별로 단언한다.

비어 있던 것은 그 예외가 응답이 되는 지점이다. `app/main.py` 의 예외 핸들러는
`ProfileMismatchError`(422) 하나뿐이었고, 분류된 나머지는 uvicorn 까지 올라가 500 이
됐다.

> 이 결함이 안 보인 이유가 완료 조건에 있다. 클라이언트 테스트는 예외가 던져지는
> 것까지 보고, API 테스트는 Fake 를 꽂아 성공 형식만 봤다. 두 파일 사이에 계층 하나가
> 통째로 비어 있었고, 각자는 자기 범위에서 통과하고 있었다.

---

## 1. 조사 — 무엇이 실제로 사실인가

### 1.1 명세가 API 층 변환을 말하지 않았다

`failure-recovery.md` §2.1·§2.2 는 분류별 **동작**을 정하지만 그것은 Context 처리 경로의
이야기다("상태를 PROCESSING 으로 둔다", "해당 단계만 FAILED 로 전이한다"). 검색은
사용자 요청 경로라 바꿀 상태가 없고 분류가 곧 응답인데, 그 변환 규칙이 어느 문서에도
없었다.

`personal-search.md` 도 §6 반환 형식에서 성공 응답만 정했다.

코드만 고치면 같은 공백이 남는다. 그래서 `failure-recovery.md` §2.5 와
`personal-search.md` §6.2 를 함께 넣었다.

### 1.2 `back` 은 500 과 503 을 구분하지 않는다 — 사용자 화면은 이미 같다

`AiSearchClient.translate` 를 읽었다.

```java
// 422 + serverProfile 필드  → AiSearchException.profileMismatch()
// 401 · 403                → AiSearchException.unavailable()
// 그 밖의 모든 상태 코드     → AiSearchException.unavailable()
```

`SEARCH_UNAVAILABLE` 은 `HttpStatus.SERVICE_UNAVAILABLE` 이다. 즉 AI 가 `500` 을
주든 `503` 을 주든 사용자는 「검색을 일시적으로 사용할 수 없습니다」를 본다. 따라서
이 티켓은 사용자 화면을 바꾸지 않는다.

`ai#69` 의 두 번째 답변이 *"AI 가 503 을 주면 back 이 「일시적으로 사용할 수
없음」으로 전달한다"* 고 적은 것은 맞지만, `500` 도 이미 그렇게 전달되고 있다는
사실이 빠져 있다. `back` 은 응답 본문도 읽지 않는다. `serverProfile` 을 보는 422
말고는 버린다.

그러면 이 변경의 실질적 가치는 어디에 있는가. 운영자가 보는 관측 정보다.

| 상태 | 의미 |
|---|---|
| 지금 | 전부 500. "AI 가 깨졌나 GMS 가 깨졌나"를 로그 없이 구분할 수 없다 |
| 이후 503 | 게이트웨이 장애·타임아웃. 기다리면 낫는다 |
| 이후 502 | 키·모델명·base URL·차원. 배포 설정을 고쳐야 낫는다 |
| 이후 500 | 우리 코드의 결함. 비로소 알림 대상이 된다 |

인프라가 `ai#69` 를 연 계기가 *"원인 불명의 500"* 이었다. 그 상태 코드가 의미를 되찾는 것이
이 티켓의 실질이다.

### 1.3 핸들러 부재는 500 만 낸 게 아니었다

대조군 서버(`origin/dev`)의 로그를 보다 발견했다. 예외가 uvicorn 까지 올라가면 트레이스백에
**예외 메시지가 그대로 찍히고**, 그 메시지는 이렇게 만들어진다.

```python
f"embedding error: {resp.status_code} {resp.text[:200]}"   # embedding_client.py
```

게이트웨이 응답 본문 200자가 들어간다. 스텁이 실제 게이트웨이처럼 키 힌트를 담은 401 을
돌려주자 로그에 그대로 나왔다.

```
app.core.errors.PermanentError: embedding error: 401 Unauthorized:
api key sk-live-DEADBEEF rejected by gms.ssafy.io
```

실제 GMS 가 무엇을 담는지는 우리가 통제하지 않는다. `probe.py` 가 세운
*"credential·endpoint·profile 값을 어떤 분기에서도 싣지 않는다"* 와 `-197` 이 로그로
확장한 같은 기준(§2.4 원칙 4)에 어긋난다. 핸들러가 이 누출도 막는다.

---

## 2. 결정

### 2.1 `TransientError` → 503, `PermanentError` → 502

`503` 은 이론의 여지가 없다. `502` 를 고른 근거는 500 을 「우리 코드의 결함」 전용으로
비워 두는 것이다.

티켓은 `502 또는 500` 으로 열어 뒀지만, `PermanentError` 를 500 으로 내면 분류된
실패와 분류되지 않은 예외가 같은 코드를 쓰게 되어 §1.2 에서 얻으려던 구분이 절반만
남는다. 「업스트림이 우리 요청을 거절했다」는 `502 Bad Gateway` 의 사전적 의미와도
맞는다.

`back` 이 재시도를 억제하도록 신호를 준다는 논거는 쓰지 않았다. `AiSearchClient` 는
애초에 재시도하지 않는다(사용자 요청 경로라 기다릴수록 손해다). 이 구분이 바꾸는
것은 호출자의 동작이 아니라, 운영자가 무엇을 고쳐야 하는가에 대한 정보다.

### 2.2 응답 본문은 고정 문구 한 줄

```json
{ "detail": "embedding upstream unavailable" }
{ "detail": "embedding upstream rejected the request" }
```

원인을 더 담지 않은 이유가 셋이다.

1. **`back` 이 본문을 읽지 않는다**(§1.2). 풍부하게 만들어도 아무도 파싱하지 않는다.
2. 업스트림 상태 코드를 실으려면 예외에 그 값을 얹어야 하는데, 같은 값이 이미
   `app.client.gms` 계측에 `status=502 outcome=transient` 로 남는다(`-197`).
3. 예외 메시지 원문을 그대로 실으면 §1.3 의 누출을 응답으로 옮기는 셈이 된다.

핸들러 로그도 예외 **타입 이름만** 남긴다. 같은 이유다.

### 2.3 `back` 변경은 하지 않는다

`back` 이 `503` 을 이미 다루므로(§1.2) 이 티켓만으로 계약이 성립한다. 사용자 화면에서
「설정 문제」와 「일시 장애」를 가르려면 `back` 이 `502` 를 별도 `ErrorCode` 로 받아야 하고,
그것은 **소유 경계는 우리지만 별건**이다. 필요하다고 판단되면 별도 티켓으로 낸다.

---

## 3. 검증

### 3.1 계약 테스트 — `tests/test_api_error_contract.py`

`httpx.MockTransport` → **실제** `EmbeddingClient` → `SearchService` → router → 예외 핸들러
→ HTTP 응답까지 한 요청으로 관통한다. 18건.

Fake client 를 쓰지 않는 것이 이 파일의 요점이다.
`FakeEmbeddingClient(raise_exc=...)` 로 예외를 주입하면 핸들러 매핑은 보이지만 분류
경로를 건너뛴다. 그러면 `classify_http_status` 가 바뀌어도 테스트가 통과한다.
`ai#69` 를 놓친 구멍이 정확히 그 형태였다.

`tests/README.md` 의 「인터페이스 레벨 Fake」 규칙은 *파이프라인이 client 를 무엇으로
대체하는가*의 규칙이고(`integration-tests.md` §4.2), 여기서 고정하는 것은 client 가 아니라
**업스트림 상태 코드와 응답 상태 코드 사이의 계약**이다. `test_client_retry.py` 가 같은
근거로 §5 계층 밖에 있는 것과 같다.

| 무엇을 | 단언 |
|---|---|
| `502` 재시도 소진 | `503`, 업스트림 호출 3회 |
| `429`·`500`·`502`·`503` | 전부 `503` |
| 타임아웃 | `503`, 3회 |
| `400`·`401`·`403`·`404` | `502`, 호출 **1회**(재시도 없음) |
| 차원 불일치(200 인데 못 씀) | `502` |
| 분류 밖 예외 | **`500` 유지** |
| 502 두 번 뒤 성공 | `200` — 전체 경로가 정상 동작한다 |
| Profile 불일치 | `422` 유지, 임베딩 미호출 |
| 시크릿 누락 | `401`, 임베딩 미호출 |
| 오류 응답 본문 | credential·endpoint·profile·검색어 **없음** |

### 3.2 구현 전 실패 확인 → 구현 후 통과

핸들러를 넣기 전에 14건이 실패하는 것을 확인했다. 실패 형태가 「500 이 나왔다」가
아니라 예외가 응답 계층으로 그대로 새는 것이었고, 그것이 운영에서 uvicorn 이 500 을
만드는 바로 그 지점이다.

핸들러 추가 후 18건 전부 통과, 전체 309건 통과. coverage line 99.8% / branch 98.7%.

### 3.3 실서버 대조 — 502 를 만들어 503 을 봤다

테스트만으로는 「핸들러 코드를 넣었다」까지만 확인된다. 실제 동작을 보기 위해 로컬
GMS 스텁(`/gmsapi/api.openai.com/v1`)을
띄우고 시연 DB(`:15432`, preset 27건)에 붙인 uvicorn 두 대를 같은 스텁에 물려 대조했다.

| 업스트림 | `origin/dev`(a5e1142) | 이 브랜치 |
|---|---|---|
| `502` | **HTTP 500** `Internal Server Error` | **HTTP 503** `{"detail":"embedding upstream unavailable"}` |
| `401` | **HTTP 500** `Internal Server Error` | **HTTP 502** `{"detail":"embedding upstream rejected the request"}` |
| `200` | — | **HTTP 200** 실데이터 5건 |

이 브랜치 서버 로그.

```
WARNING app.client.gms   gms call kind=embedding status=502 outcome=transient ms=15
WARNING app.client.retry stage=embedding transient error, retry in 0.327s (2 attempt(s) left)
WARNING app.client.gms   gms call kind=embedding status=502 outcome=transient ms=0
WARNING app.client.retry stage=embedding transient error, retry in 0.290s (1 attempt(s) left)
WARNING app.client.gms   gms call kind=embedding status=502 outcome=transient ms=16
WARNING app.main         upstream transient failure: TransientError
INFO:    "POST /internal/v1/search HTTP/1.1" 503 Service Unavailable
```

`502` 응답에 1.06초가 걸렸다. 재시도 두 번의 백오프가 그 안에 있다. `401` 은
0.33초로, 재시도가 없다.

---

## 4. 남은 문제

- **DB 실패는 여전히 500 이다.** `failure-recovery.md` §2.1 은 "DB 연결 실패, 잠금
  타임아웃, 직렬화 실패"를 일시 오류로 두지만 `SearchService` 는 asyncpg 예외를
  분류하지 않는다. 검색 중 DB 접속이 실패하면 `503` 이 아니라 `500` 이 나간다. 이
  티켓의 범위 밖이며, 고치려면 repository 층에 분류를 넣어야 한다. 별도 티켓 후보다.
- **Circuit breaker 를 넣지 않았다.** `failure-recovery.md` §2.2 가 언급하지만 미구현이고
  이 티켓이 다루는 문제가 아니다.
- **`Retry-After` 헤더를 붙이지 않았다.** `back` 이 재시도하지 않으므로 소비자가 없다.
- **`/internal/v1/context/process` 는 영향이 없다.** 202 를 먼저 반환하고 백그라운드로 도는
  구조라 예외가 응답이 되지 않으며, 그 경로의 분류별 동작은 §2.1·§2.2 가 이미 정하고
  `ContextProcessingService` 가 자체적으로 잡는다.

## 관련 장애 기록

로컬 재현 중 발견한 문제 세 건을
[2026-07-31-error-contract-pitfalls.md](../troubleshooting/2026-07-31-error-contract-pitfalls.md)
(T43~T45)에 남겼다.

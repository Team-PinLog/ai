# GMS 호출·재선점 로그 계측 — 실패율·지연·재선점을 로그로 볼 수 있게 했다

- **티켓**: S15P11A705-197

`dev`의 세 gate가 전부 열렸는데 무엇이 실패하는지 볼 수단이 없었다. 그 공백을
메웠다. 이번 범위는 로그까지이며 `/metrics` 엔드포인트는 만들지 않았다.

## 1. 무엇이 없었나

[티켓 감사 리포트](2026-07-31-ticket-audit-96-77.md) §4-c가 관측 3종을 그대로 적어 둔
것이 착수 근거다.

| 항목 | 당시 | 원인 |
|---|---|---|
| GMS 요청 성공/실패/지연 | **없음** | 두 클라이언트에 로거가 없었다. 재시도 소진 경고 1건뿐(`retry.py`) |
| embedding/judge 처리 결과 | **부분** | 영구 오류만 `log.error`. 성공·지연은 없음 |
| stale recovery 수행/실패 | **없음** | `try_start`가 신규 시작과 재선점을 같은 경로로 처리하고 어느 쪽도 로그가 없다 |

왜 지금이냐면, **GMS 쿼터가 시점·경로에 따라 다르기 때문**이다. 2026-07-29에 "분당 2건"
으로 관측된 것이 다음 날 같은 코드로 분당 30건을 통과시켰다(`-176` 실측). 실패율을 못
보면 시연 중 느려졌을 때 우리 문제인지 게이트웨이 문제인지 구분할 수 없다.

## 2. 네 가지 판단

### 2.1 `_usage.py`와 합치지 않았다

같은 호출을 두 곳에 적는 것이 맞느냐는 물음에 대한 답은 **역할이 다르므로 합칠 수
없다**이다.

| | `_usage.py` (`-174`) | `_calls.py` (이번) |
|---|---|---|
| 목적 | 비용 집계(토큰) | 실패율·지연 |
| 게이트 | `PINLOG_TOKEN_LOG` 있을 때만 | 항상 |
| 출력 | JSONL 파일 | 표준 로깅(stdout) |
| **기록 시점** | **200 응답을 파싱한 뒤** | 호출이 끝날 때 (성공·실패 무관) |

마지막 줄이 결정적이다. `_usage.record()`는 실패한 호출에서 **한 줄도 남기지 않는다** —
실패율의 분자가 애초에 그 파일에 없다. 게이트가 환경변수라 dev 배포에서는 파일 자체가
만들어지지 않으므로 분모도 없다. 반대로 로그로 합치면 토큰 집계가 실패 행과 섞이고,
JSONL을 읽는 도구(`-174`의 집계)가 깨진다.

중복은 벤더·모델 두 필드뿐이고, 그것은 두 목적 모두에 필요한 값이다.

### 2.2 재선점은 쿼리를 갈라야 구별된다

`ai_state_repo.try_start`는 `PENDING`(신규 시작)과 만료된 `PROCESSING`(재선점)을 같은
UPDATE로 처리하고 둘 다 rowcount `1`을 돌려준다. 로그만으로 가를 수 있는지 따져 보면
**두 곳에서 틀린다.**

- **Keyword 단계는 근거가 아예 없다.** `keyword_service.run`은 `try_start` 전에 상태를
  읽지 않고 곧장 부른다.
- **Embedding 단계는 거짓 양성이 섞인다.** `load_resume`과 UPDATE 사이에 경합 창이 있어,
  읽은 값이 `PROCESSING`이어도 실제로 재선점했다는 보장이 없다.

재선점은 드물지만 중요한 신호다. 재선점이 났다는 것은 앞선 처리가 만료(600초) 안에
끝나지 못했다는 뜻이고, 원인은 프로세스 종료 아니면 GMS 지연 둘뿐이다. 거짓 양성이
섞이면 그 신호 자체를 믿을 수 없게 되므로 정확도를 포기할 수 없었다.

그래서 UPDATE 직전 상태를 같은 SQL 문장 안에서 함께 읽는다.

```sql
WITH prev AS (
    SELECT embedding_status AS status FROM ai.context_ai_state WHERE context_id = $1
),
started AS (
    UPDATE ai.context_ai_state SET embedding_status = 'PROCESSING', updated_at = now()
    WHERE ... RETURNING 1
)
SELECT (SELECT count(*) FROM started) AS affected, (SELECT status FROM prev) AS prev_status
```

두 CTE가 같은 스냅샷을 보므로 `prev`는 UPDATE 이전 값이다. 추가 조회가 아니므로
[state-machine.md](../spec/state-machine.md) §3.2의 *"0인 이유를 알기 위해 다시 SELECT하지
않는다"*와 충돌하지 않는다 — 이쪽은 **`1`이 무엇이었는지**를 묻는다.

스냅샷과 UPDATE가 갈릴 수 있는 경합은 `affected > 0`과 함께 성립하지 않는다. 남이
`PENDING`을 선점했다면 만료 조건에 걸려 우리 UPDATE가 0행이 되고, 반대 방향
(`PROCESSING` → `PENDING`)은 어느 주체에게도 허용되지 않는다(§2). 즉 `reclaimed`가 참일
때 그 값은 정확하다.

### 2.3 벤더·모델은 안전하고, 그 밖은 싣지 않는다

[probe.py](../../app/api/probe.py)가 세운 기준(*"credential·endpoint·profile 값을 어떤
분기에서도 싣지 않는다"*)을 로그에 그대로 적용했다. 요청 본문은 특히 대상이다 — 거기에
사용자가 쓴 Context 원문이 있다.

**벤더·모델 이름은 싣는다.** 공개 설정이고 정본이 코드에 있으며([P45](../proposals/P45-public-config-in-code.md)),
`VendorCall.label`이 이미 *"모델명은 공개 설정이므로 값 노출 제약이 없다"*고 적어 둔
값이다. 그리고 이것을 빼면 로그에 남는 값이 사라진다. 폴백 체인에서는 어느 경로가
막혔는가가 곧 원인이기 때문이다(§3.4의 "쿼터는 경로별로 걸린다").

전송 실패에서 예외 메시지를 쓰지 않고 타입 이름만 쓰는 것도 같은 이유다. httpx는
예외 메시지에 요청 URL을 넣는다.

### 2.4 정상 호출은 `DEBUG`, 분모는 창 집계

성공까지 `INFO`로 남기면 dev 로그가 GMS 호출로 뒤덮여 실패 행을 못 찾는다. 그렇다고
성공을 아예 안 세면 실패율의 분모가 없다. 그래서 개별 행은 결과가 레벨을 가르고
(`DEBUG`/`WARNING`/`ERROR`), 60초 창 집계 한 줄만 `INFO`로 낸다.

집계를 내보내는 계기는 타이머가 아니라 다음 호출이다. 호출이 없으면 요약도 나오지
않으므로 유휴 상태의 로그가 조용하다. 대가는 마지막 창이 남지 않는 것이다. lifespan
`finally`의 `flush()`가 그것을 처리한다. 시연이 끝나고 파드를 내리는 순간의
실패율이 정확히 그 마지막 창에 해당하기 때문이다.

전체 레벨 표는 [failure-recovery.md](../spec/failure-recovery.md) §2.4가 정본이다.

## 3. 실제로 나오는 것

운영과 같은 `configure_logging()`(기본 `INFO`) 아래 실행 출력이다.

```text
WARNING app.client.gms gms call kind=judge vendor=gemini model=gemini-2.5-flash status=429 outcome=transient ms=0
WARNING app.client.gms gms call kind=judge vendor=gemini model=gemini-2.5-flash status=401 outcome=permanent ms=0
WARNING app.client.gms gms call kind=judge vendor=gemini model=gemini-2.5-flash status=200 outcome=schema ms=0
WARNING app.client.gms gms call kind=embedding vendor=- model=text-embedding-3-small status=429 outcome=transient ms=0
INFO    app.client.gms gms window window=71s calls=10 fail=4 fail_pct=40 avg_ms=1240 max_ms=2500 ok=6 transient=4 [judge:gemini ok=6 transient=4]
WARNING app.service.stage ctx=1 stage=embedding reclaimed stale PROCESSING (expiry=600s)
WARNING app.service.stage ctx=1 stage=keyword reclaimed stale PROCESSING (expiry=600s)
```

`status`와 `outcome`이 둘 다 있는 이유는 조합이 진단이기 때문이다.

| 조합 | 읽는 법 |
|---|---|
| `status=429 outcome=transient` | 게이트웨이 쿼터. 그 벤더 경로가 막혔다 |
| `status=200 outcome=schema` | 게이트웨이는 멀쩡했고 모델 출력이 깨졌다. 처방이 정반대다 |
| `status=200 outcome=permanent` | (임베딩) 차원 불일치 — Profile이 어긋난 배포 |
| `status=401 outcome=permanent` | 키·설정. 폴백도 재시도도 소용없다 |
| `status=ConnectTimeout` | 응답 자체가 없었다 |
| `outcome=unclassified` | **오류 분류가 새고 있다.** 그 단계는 PROCESSING에 머문다 |

## 4. 계측을 붙이다 발견한 것 — httpx가 요청 URL을 로그에 남기고 있었다

테스트는 전부 초록인데 실행 출력을 눈으로 보니 우리 행 바로 위에 이것이 있었다.

```text
INFO httpx HTTP Request: POST https://<gms-host>/gmsapi/generativelanguage.googleapis.com/
     v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 429 Too Many Requests"
```

httpx가 요청마다 INFO 레벨로 전체 URL을 남기고 있었다. 두 가지가 동시에 깨져
있었다. §2.3의 endpoint 미노출 기준을 정면으로 어기고, §2.4의 "성공은 조용하게"를
무의미하게 만든다(성공한 호출도 이 줄은 INFO로 나온다).

`caplog`를 로거 이름으로 걸러 읽는 테스트가 이것을 잡지 못했다. 자기 로거만 보므로
다른 로거에서 무엇이 새고 있어도 통과한다. `configure_logging()`이 `httpx` 로거를
`WARNING`으로 올리게 하고, 로거를 가리지 않고 전부 읽는 테스트를 따로 두어 고정했다.

정보는 잃지 않는다. 같은 호출에 대해 `app.client.gms`가 더 나은 행을 낸다. 그 행에는
결과 분류와 소요 시간이 있고 URL은 없다.

## 5. 검증

| 항목 | 결과 |
|---|---|
| `tests/test_gms_call_log.py` (신설, 21건) | MockTransport로 429·503·401·400·타임아웃·깨진 봉투·차원 불일치를 만들어 각 행을 단언 |
| 값 노출 | 키·URL·Context 원문·응답 본문이 성공/실패 **양 분기** 모두에서 로그에 없음 |
| `tests/test_pipeline.py` (3건 추가) | Testcontainers 실 DB로 stale `PROCESSING`을 만들어 두 단계의 재선점 행 확인. 신규 시작은 0건 |
| `tests/test_repo.py` (5건 추가) | `prev_status`·`reclaimed` 조합, keyword 가드가 CTE 재작성 후에도 유지되는지 |
| 전체 | 254 → **283 passed** · line 99.81% · branch 98.72% · `check_coverage_gate.py` ok |

미달로 남은 2 line · 2 branch는 이번 변경과 무관하다. `FOR UPDATE`로 잠근 직후의
재검사라 같은 트랜잭션에서 도달할 수 없는 줄이고, `-110`에서 `pragma`를 붙이지 않고
미달로 두기로 한 그 두 줄이다(붙이면 나중에 도달 가능해져도 아무도 모른다).

"로그 코드를 넣었다"를 검증으로 치지 않았다. 실패를 실제로 만들어 행이 나오는 것을
확인했고, §4는 그렇게 하지 않았으면 놓쳤을 결함이다.

## 6. 하지 않은 것

- **`/metrics` Prometheus 엔드포인트** — `infra` `docs/ai-serving.md` 검증 순서 7이
  metrics·alert·부하를 prod 승격 전 별도 승인 항목으로 이관했다.
- **오류 메시지의 `resp.text[:200]`** — 게이트웨이 오류 본문 200자를 예외 메시지에 싣는
  기존 동작은 그대로 두었다. 400이 왜 났는지는 그 문장에만 있어 진단에 필요하고, 이번
  범위는 새로 만드는 행이다. 다만 게이트웨이가 오류 응답에 URL을 에코하면 그 경로로
  endpoint가 로그에 남을 수 있다 — 별도 판단이 필요한 항목으로 남긴다.
- **`uvicorn.access` 정리** — 프로브 요청이 주기적으로 찍히지만 접근 로그는 그 자체로
  쓸모가 있고, httpx와 달리 우리 로그와 중복되지 않는다.

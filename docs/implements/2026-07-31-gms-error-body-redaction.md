# 게이트웨이 오류 본문 마스킹 — 로그로 새는 경로를 원천에서 막는다

- **티켓**: S15P11A705-205
- **상태**: 완료
- **날짜**: 2026-07-31
- **선행**: [GMS 호출 계측](2026-07-31-gms-call-observability.md) (`-197`) ·
  [검색 오류 응답 계약](2026-07-31-search-error-contract.md) (`-220`) ·
  [DB 오류 분류](2026-07-31-db-error-classification.md) (`-221`)
- **관련**: [`ai#69`](https://github.com/Team-PinLog/ai/issues/69) — 운영 로그 조치는 인프라 소관

## 이 문서가 다루는 것

`-220`이 응답 **본문**을 막았고, 이 티켓이 **로그**를 막는다.

```
resp.text[:200] → 예외 메시지 → 다섯 곳의 로그 + 트레이스백
```

`-220`은 예외가 HTTP 응답이 되는 지점에 핸들러를 넣어 본문을 고정 문구로 바꿨다. 같은
예외 메시지가 **로그로도 나간다**는 것은 그 티켓의 범위가 아니었고, 그대로 남아 있었다.

---

## 1. 실측 — 세 벤더가 오류 본문에 무엇을 담는가

**티켓이 세운 가설과 다르다.** 티켓은 *"게이트웨이가 요청 URL을 에코한다 → endpoint
누출"*로 위험을 좁혔다. 실제로 재 보니 축이 둘 더 있었고, **가장 걱정하던 자격 증명은
한 건도 나오지 않았다.**

### 1.1 어떻게 쟀나

실제 GMS 키로 네 경로(OpenAI·Gemini·Anthropic·임베딩)에 오류를 **의도적으로** 만들어
응답 본문을 받았다. 19건이다. 자격 증명 에코 여부를 보는 401 케이스에는 진짜 키가 아니라
가짜 값을 넣었고, 스크립트는 세션 임시 디렉터리에만 두고 커밋하지 않았다. 응답에 진짜 키가
섞여 나오면 값을 지우고 **에코됐다는 사실만** 남기도록 짰다 — 한 건도 걸리지 않았다.

| 축 | 케이스 |
|---|---|
| 자격 증명 | 잘못된 키로 401 |
| 설정 오류 | 존재하지 않는 모델명으로 400 |
| 요청 위반 | 스키마상 성립하지 않는 값으로 400 |
| 요청 값 round-trip | 요청 값에 마커를 심고 400을 유발 |

### 1.2 결과

**자격 증명 — 네 경로 모두 에코하지 않는다.** 401은 게이트웨이가 만드는 고정 문구다.
벤더에 도달하기도 전에 GMS가 끊는다.

```json
{"message":"[GMS 에러] Invalid or expired GMS key","statusCode":401}
```

**endpoint — 맨 호스트 형태로 실린다.** URL 전체는 아니었다.

```json
{"message":"[GMS 에러] Model not found in request for domain api.openai.com","statusCode":400}
```

**게이트웨이 오류와 벤더 오류가 갈린다.** 티켓의 추론대로였다. GMS가 만드는 것은
`[GMS 에러]`로 시작하고 짧으며(66~98바이트) 고정 문구다. 벤더까지 갔다 온 것은
`[<Vendor> 에러]`로 시작하고 **벤더 원문을 `error` 필드에 통째로 중첩**한다(215~433바이트).

| 경로 | 400 본문(앞 200자) |
|---|---|
| OpenAI | `{"message":"[OpenAI 에러] Request failed with status code 400","statusCode":400,"error":{"error":{"message":"Invalid 'max_completion_tokens': integer below minimum value. Expected a value >= 1, but got …` |
| Gemini | `{"message":"[Gemini 에러] Request failed with status code 400","statusCode":400,"error":{"error":{"code":400,"message":"Invalid JSON payload received. Unknown name \"max_completion_tokens\": Cannot find…` |
| Anthropic | `{"message":"[Anthropic 에러] Request failed with status code 400","statusCode":400,"error":{"type":"error","error":{"type":"invalid_request_error","message":"max_tokens: must be greater than or equal to…` |
| 임베딩(OpenAI) | `{"message":"[OpenAI 에러] Request failed with status code 400","statusCode":400,"error":{"error":{"message":"Invalid 'input': input cannot be an empty array.","type":"invalid_request_error","param":null…` |

**요청 값이 잘려서 되돌아온다 — 이것이 실측의 핵심이다.** 요청 값에
`PINLOG-CTX-MARKER-205-abcdef`를 심고 400을 유발했더니 OpenAI가 이렇게 답했다.

```
Invalid value: 'PIN...def'. Supported values are: 'system', 'assistant', 'user', …
```

**앞 3자와 뒤 3자만 남기고 잘라서 에코한다.** 완전 일치로 마커를 찾는 검사는 이것을
「에코 없음」으로 판정한다(T57).

사용자 Context 원문이 실리는 자리(`messages[].content`·`parts[].text`·`input`)는 값이 아니라
**타입·경로만** 되돌아왔다 — `messages.0.content: Input should be…`, `Invalid value at
'contents[0].parts[0]' (text)`. PII가 통째로 새는 경로는 관측되지 않았다.

### 1.3 그래서 무엇이 결론인가

**"관측된 것을 지운다"로 규칙을 세우면 안 된다.** 오늘 자격 증명이 안 나온다는 사실은
게이트웨이 구현에 달려 있고 우리가 통제하지 않는다. 그리고 요청 값이 **잘려서라도**
round-trip 한다는 것이 실측으로 확인된 이상, 그 자리에 언젠가 자격 증명이 실릴 수 있다고
보는 편이 맞다. `-220`이 스텁으로 재현한 누출이 정확히 그 모양이었다.

규칙의 근거는 **"되돌아올 수 있는 자리를 막는다"**다.

> **아무것도 안 새는 벤더**: 자격 증명 기준으로는 **네 경로 모두**다. 근거는 §1.2의
> 401 고정 문구 — 벤더가 답하기 전에 게이트웨이가 끊으므로 벤더별 차이가 생길 자리가
> 없다. endpoint 기준으로는 **네 경로 모두 샌다**(맨 호스트). 벤더가 갈리는 축은
> 자격 증명이 아니라 **본문 길이와 중첩 깊이**였다.

---

## 2. 로그 경로 전수

응답 본문만 보면 절반을 놓친다. `resp.text`가 예외 메시지에 들어간 뒤 **그 메시지가
어디서 로그가 되는가**를 셌다.

| # | 위치 | 레벨 | 어떻게 |
|---|---|---|---|
| 1 | `app/client/retry.py:107` | `WARNING` | `str(exc)` — **재시도 1회마다 한 줄** |
| 2 | `app/service/embedding_service.py:64` | `ERROR` | `%s`에 예외 객체 |
| 3 | `app/service/embedding_service.py:71` | `WARNING` | 〃 |
| 4 | `app/service/keyword_service.py:106` | `ERROR` | 〃 |
| 5 | `app/service/keyword_service.py:111` | `WARNING` | 〃 |
| 6 | uvicorn 트레이스백 | `ERROR` | 분류 밖 예외의 메시지가 그대로 |

**티켓이 적은 「분류 밖 예외 → 트레이스백」은 여섯 중 하나다.** 나머지 다섯은 **분류가
정상 동작하는 경로**이며, 그쪽이 훨씬 자주 실행된다 — 1번은 `502` 한 번에 두 줄이 난다.
즉 **막힌 것은 트레이스백이 아니라 평상시 경로**였다.

계약이 *"분류 밖 예외는 여전히 500으로 나가며 트레이스백을 남긴다"*를 남은 구멍으로
지목한 것은 맞지만 **좁았다.** `-221`의 「애매한 것은 분류하지 않는다」와 무관하게, 분류에
성공해도 같은 문자열이 샜다.

### 2.1 명세와 코드가 어긋나 있었다

`failure-recovery.md` §2.4 원칙 4는 이렇게 적혀 있었다.

> credential·endpoint·**요청/응답 본문**은 어느 레벨에서도 남기지 않습니다.

`-197`이 이 문장을 쓸 때 `app.client.gms`가 내는 행만 보고 썼다. 그 로거는 실제로 본문을
싣지 않는다. 그러나 같은 표에 있는 `app.service.*`와 `app.client.retry` 행은 예외 객체를
`%s`로 받는다. **문장은 참이 아니었고, 아무도 그것을 몰랐다.** §2.6으로 갈라 고쳤다.

### 2.2 응답 없는 실패도 원천이다

`llm_client.py:147`의 주석이 이렇게 적혀 있다.

> 메시지 본문에는 URL 이 섞여 들어올 수 있어 그쪽이 아니라 타입 이름을 쓴다.

맞는 판단이지만 **그 바로 다음 줄**이 같은 예외를 `{exc}`로 메시지에 넣는다. 계측
필드(`rec.status`)만 지키고 예외 메시지는 열려 있었다. 임베딩 쪽도 같다. 둘 다 마스킹을
걸었다.

---

## 3. 결정

### 3.1 막는 지점을 로그 호출부가 아니라 client로 잡았다

호출부는 여섯이고 그중 하나(트레이스백)는 **애초에 호출부가 없다.** 호출부마다 가리면
§2의 표를 사람이 관리해야 하고, 다음에 늘어나는 일곱 번째를 놓친다. 예외 메시지 자체가
깨끗하면 어디서 찍히든 안전하다.

대안으로 **root 로거에 `logging.Filter`**를 거는 방법이 있었다. 트레이스백까지 한 번에
덮지만 채택하지 않았다 — 모든 로그 행에 정규식을 물리는 비용이 상시로 들고, 우리와 무관한
라이브러리 로그까지 문자열을 건드린다. 원천이 하나뿐인데 하류 전체를 검사할 이유가 없다.

### 3.2 자격 증명이 endpoint보다 먼저다

계약이 지정한 우선순위다. 순서가 실제로 결과를 가른다 — `https://host/?key=sk-…`에서
URL 규칙이 먼저 걸리면 키가 통째로 `<url>`에 삼켜져 **"무엇이 지워졌는지"가 사라진다.**
지워진 것이 키인지 경로인지는 대응이 갈리는 정보다(키면 재발급, 경로면 설정 수정).

값만 지우고 **이름은 남긴다**. `"apiKey": "***"`가 `***`보다 낫다.

### 3.3 마스킹이 절단보다 먼저다

`resp.text[:200]`을 그대로 두고 그 뒤에 마스킹하면, 200자 경계에 걸친 키의 **앞부분이
잘린 채 남는다.** 잘린 자격 증명도 자격 증명이다 — §1.2에서 OpenAI가 값을 잘라 에코하는
것을 봤으므로 이건 가정이 아니다.

그래서 `redact_body()`는 `redact(text[:4096])[:200]` 순서다. 출력은 마스킹을 마친 문자열의
부분 문자열이므로, 마스킹을 우회해 출력에 들어가는 경로가 없다. `4096`은 병적으로 긴
본문에서 마스킹이 요청보다 비싸지지 않게 하는 상한이다.

### 3.4 맨 호스트까지 지운다 — 진단을 잃지 않는다

`Model not found in request for domain api.openai.com`에서 호스트를 지우면 문장이
`… for domain <host>`가 된다. 잃는 것이 없다 — **어느 벤더였는지는 같은 시각의
`app.client.gms` 행이 `vendor=openai`로 이미 말한다**(`-197`).

TLD를 화이트리스트로 한정했다. 점이 든 평범한 식별자를 건드리지 않기 위해서다.

```
지운다        api.openai.com · gms.ssafy.io · generativelanguage.googleapis.com
안 건드린다   messages.0.content · contents[0].parts[0] · gemini-2.5-flash · text-embedding-3-small
```

### 3.5 본문 200자를 지우지 않는다

티켓의 확정 판단이고 실측이 이를 뒷받침한다. §1.2의 벤더 400 본문에서 마스킹 대상은
**한 글자도 없다** — 전부 진단 문구다. 무조건 지웠으면 `Invalid 'max_completion_tokens'`,
`max_tokens: must be greater than or equal to 1` 같은 **원인 그 자체**를 버렸을 것이다.

이 네 본문이 그대로 통과하는지를 테스트로 고정했다(§4).

---

## 4. 검증

### 4.1 RED → GREEN

`tests/test_log_redaction.py` 18건. 마스킹 모듈을 넣기 전 **동작 단언 6건이 RED**였고,
실패 형태가 곧 §2의 표였다 — 예외 메시지·재시도 로그·전체 로그 행·트레이스백·전송 실패
메시지·구조 방어.

`app/core/redact.py`와 두 client를 고친 뒤 18건 GREEN.

| 무엇을 | 단언 |
|---|---|
| 자격 증명 5형태 | `redact()` 후 값이 남지 않는다 |
| endpoint — URL·맨 호스트 | 둘 다 지워지고 주변 문구는 남는다 |
| **실측한 GMS·벤더 본문 4종** | **마스킹을 통과해 원문 그대로다** |
| 200자 경계에 걸친 키 | 앞부분도 남지 않는다 |
| 임베딩·판정 `PermanentError` | `str(exc)`와 **트레이스백**에 값이 없다 |
| 재시도 `WARNING` | `app.client.retry` 행에 값이 없다 |
| 호출이 남긴 **모든** 로그 행 | 계측·재시도를 합쳐 전수로 훑는다 |
| 응답 없는 전송 실패 | httpx 예외 메시지의 URL이 지워진다 |
| 구조 방어 | `app/` 전체에 마스킹 없는 본문 접근이 없다(AST) |

### 4.2 구조 방어를 AST로 본다

`test_no_unredacted_response_body_in_app`은 `app/` 전체를 파싱해 `resp.text` 접근 노드를
모으고, `redact_body(...)` 인자 안에 든 것을 뺀 나머지를 위반으로 낸다.

처음에 텍스트로 훑었더니 `gms_roundtrip.py`의 **docstring**이 위반으로 잡혔다 — 그 파일은
`resp.text[:200]`을 *설명*하고 있었다. 산문을 코드로 오인하는 검사는 오래 못 간다(T59).

### 4.3 전체

```
ruff check .                  All checks passed
python -m compileall app tools exit 0
pytest                        375 passed
coverage                      line 99.82% · branch 98.85%   (게이트 80/80)
```

### 4.4 하지 않은 것

- **실서버 대조를 하지 않았다.** `-220`처럼 uvicorn 두 대를 띄워 로그를 눈으로 비교하는
  절차는 밟지 않았다. 이 변경은 응답이 아니라 **로그 문자열**을 바꾸므로 계약 테스트가
  보는 것과 실서버가 내는 것이 같다 — `caplog`이 실제 로거 레코드를 받는다. 대신 §1의
  실측을 **실제 GMS로** 했고, 그 본문이 테스트 픽스처의 정본이다.
- **429 본문을 재지 못했다.** 공용 게이트웨이를 의도적으로 밀어 429를 만드는 것은 같은 키를
  쓰는 다른 세션·시연에 영향이 간다. `-197`이 기록한 것은 상태 코드와 헤더 줄뿐이라
  본문 형태는 미상이다. 429가 `[GMS 에러]` 계열이면 §1.2와 같고, 벤더 원문을 중첩하면
  §1.2의 벤더 계열과 같다 — 어느 쪽이든 마스킹 규칙은 그대로 적용된다.

---

## 5. 남긴 것

- **`tools/` 는 범위 밖이다.** `tools/keyword_eval/probe_vendors.py`·`tools/demo_seed/_client.py`
  등이 `r.text`를 그대로 출력한다. 측정 도구는 사람이 손으로 돌리고 출력이 컨테이너 로그로
  가지 않으므로 위험이 다르다. 구조 방어 테스트도 `app/`만 본다. 필요해지면 별도 티켓.
- **`_usage.py`의 토큰 로그는 건드리지 않았다.** `PINLOG_TOKEN_LOG`가 있을 때만 동작하고
  **200 응답만** 기록한다 — 오류 본문이 닿지 않는다.
- **`vendors.py`의 `_unwrap`은 200 응답의 봉투를 파싱하다 난 예외를 싣는다.** 키 이름 같은
  구조 정보라 위험이 낮다고 보고 그대로 뒀다. 여기에 모델 출력 값이 실릴 여지는 남는다.
- **운영 로그 조치는 이 티켓 밖이다.** 이미 남은 로그의 Loki 검색과 키 재발급 판단은
  `ai#69`로 인프라 파트에 있다.
- **`/metrics`는 건드리지 않았다** — prod 승격 전 승인 항목(§2.4).
- **거대 요청 본문에서 GMS가 오해를 부르는 400을 낸다**(T58). 이 티켓의 범위는 아니지만
  분류에 영향이 있다 — 자세한 것은 트러블슈팅.

## 관련 함정

측정·구현 중 걸린 셋을
[2026-07-31-log-redaction-pitfalls.md](../troubleshooting/2026-07-31-log-redaction-pitfalls.md)
(T57~T59)에 남겼다.

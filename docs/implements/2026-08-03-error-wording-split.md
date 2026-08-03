# 오류 응답 문구 분리 — DB 실패가 더 이상 임베딩을 가리키지 않는다

- **티켓**: S15P11A705-229
- **상태**: 완료
- **날짜**: 2026-08-03
- **선행**: [검색 API 오류 응답 계약](2026-07-31-search-error-contract.md) (`-220`) ·
  [DB 실패의 오류 분류](2026-07-31-db-error-classification.md) (`-221`) ·
  [게이트웨이 오류 본문 마스킹](2026-07-31-gms-error-body-redaction.md) (`-205`)
- **명세**: [`failure-recovery.md`](../spec/failure-recovery.md) §2.5 「응답 본문」

## 이 문서가 다루는 것

`-221`이 남긴 한계 하나다. `-221`은 검색 중 DB 실패를 `503`/`502`로 정확히 분류했지만
(`app/core/db_errors.py`), 응답 **본문**은 `-220`이 정한 고정 문구 `embedding upstream
...` 그대로 두었다 — 상태 코드·로그는 DB를 가리키는데 본문만 게이트웨이를 가리키는
상태였다.

```
DB 연결 실패   → 503   "embedding upstream unavailable"       ← 층이 틀렸다
DB 인증 실패   → 502   "embedding upstream rejected the request" ← 층이 틀렸다
```

`-221`이 이 한계를 남긴 이유는 명확했다 — 티켓이 *"핸들러를 바꾸지 않는다"*로 확정했고,
본문을 바꾸는 것은 `-220`이 정한 계약의 개정이라 범위 밖으로 미뤘다. 이 티켓이 그
개정이다.

---

## 1. 확인 — 무엇이 정말 문제였나

`-221`이 이미 보고한 사실을 재확인했다. 상태 코드는 정확했다(`test_db_unreachable_returns_503`,
`test_db_misconfigured_target_returns_502` — `-221`에서 이미 초록). 로그도 갈렸다
(`app.core.db`가 `DatabaseTransientError`/`DatabasePermanentError` 타입 이름을 남긴다).
**본문 딱 하나만 사실과 달랐다.**

## 2. 결정 — 원인을 담지 않고 층 이름만 바꾼다

### 2.1 왜 예외 메시지를 싣지 않는가 (재확인, 재논의 아님)

`-220`이 예외 메시지에 업스트림 응답 본문 200자가 섞일 수 있음을 확인했고(`embedding_
client._embed_batch`), `-221`이 DB 축에서 같은 원칙을 적용해 `db_errors.py`의 예외
메시지에 타입 이름·SQLSTATE만 남기도록 했다. `-205`가 그 경로의 실제 누출(GMS 응답
200자가 다섯 곳의 로그·트레이스백으로 새던 것)을 실측으로 확정했다. 이 세 근거가
그대로 유효하므로 이 티켓에서도 본문에 예외 메시지·SQLSTATE·host·DSN 어느 것도
신지 않는다 — **값은 로그가 담당하고, 본문은 층 이름만 말한다.**

### 2.2 핸들러가 타입을 본다

`-221`이 `DatabaseTransientError(TransientError)`·`DatabasePermanentError
(PermanentError)` 하위 타입을 이미 두었으므로, `app/main.py`의 두 핸들러 안에서
`isinstance` 하나로 층을 가른다. **새 핸들러를 추가하지 않았다** — `-220`이 정한
"변환은 예외 핸들러 한 곳에서만 한다"는 원칙(라우터가 개별적으로 잡으면 `-121`과
같은 형태로 갈라진다)을 그대로 지킨다.

```python
detail = (
    "database unavailable"
    if isinstance(exc, DatabaseTransientError)
    else "embedding upstream unavailable"
)
```

`PermanentError` 핸들러도 대칭이다 — `"database rejected the request"` /
`"embedding upstream rejected the request"`.

**상태 코드 분기, 재시도 정책, `db_errors.py`의 분류 규칙(§2.5 「애매한 것은 분류하지
않는다」)은 건드리지 않았다.** 이 티켓이 바꾸는 것은 문구 딱 하나다.

## 3. `static/05` 반영 여부 — 반영하지 않는다

**판단**: 공용 계약 `Team-PinLog/docs` `static/05_AI_설계.md`는 이번 개정의 대상이
아니다.

**근거 둘.**

1. **`static/05`는 이 문구를 소유하지 않는다.** `static/05_AI_설계.md` §17이 `failure-
   recovery.md`를 "AI 파트 세부 문서"로 **참조만** 한다(직접 확인, 836줄 문서에
   `embedding upstream`·응답 본문 문구 자체는 등장하지 않는다). 고정 문구는 처음부터
   `-220`이 `failure-recovery.md`에 정의한 AI 파트 소유 계약이었고, 공용 문서는 그
   문서의 존재만 가리킨다. 존재를 가리키는 포인터는 가리키는 대상이 바뀌어도 갱신할
   내용이 없다.
2. **`back`이 이 필드의 값을 읽지 않는다.** `back` 레포 `AiSearchClient.translate`
   (`domain/ai/client/AiSearchClient.java:136-158`)를 직접 읽었다 — `422`
   응답에서만 `serverProfile` 필드 유무를 보고, `401`/`403`은 상태 코드만 보며, 그
   외 모든 상태 코드(이 티켓이 다루는 `502`/`503` 포함)는 **상태 코드만 로그에 남기고
   본문은 버린다**("응답 본문은 남기지 않는다 — 내부 API라도 로그로 새어 나갈 이유가
   없다", 155행 주석). 사용자에게는 `AiSearchException.unavailable()` 하나로 묶여
   `503 SEARCH_UNAVAILABLE`이 나가고, 이 개정 전후로 그 화면은 같다. 공용 계약이
   규정하는 것은 파트 간 **동작**이지 AI 파트가 내부적으로 관측용으로 쓰는 문구가
   아니다.

두 근거 모두 "back이 안 읽으면 반영할 이유가 약하다"는 티켓의 가설과 일치했다 — 가설
검증으로 그쳤고 새 판단을 만들지 않았다.

---

## 4. 검증

### 4.1 계약 테스트 — `tests/test_api_error_contract.py` 신규 4건 + 회귀 2건

`-220`·`-221`이 세운 원칙을 그대로 지켰다. **Fake를 쓰지 않는다** — `MockTransport`
→ 실제 `EmbeddingClient`, 실제 `asyncpg` 풀 → 실제 `SearchService` → router → 예외
핸들러 → HTTP 응답까지 한 요청으로 관통한다. 예외를 직접 주입하면 분류 경로를
건너뛰어 `db_errors.py`나 `classify_http_status`가 바뀌어도 이 파일이 통과한다 —
`ai#69`를 놓친 구멍과 같은 모양이라 여기서도 피한다.

| 테스트 | 실패시키는 방법 | 단언 |
|---|---|---|
| `test_db_transient_failure_uses_database_wording` | 닫힌 포트 `min_size=0` 풀 | `{"detail": "database unavailable"}` |
| `test_db_permanent_failure_uses_database_wording` | 없는 데이터베이스(`3D000`) | `{"detail": "database rejected the request"}` |
| `test_gms_transient_failure_wording_unchanged`(회귀) | 업스트림 502 고정 | `{"detail": "embedding upstream unavailable"}` |
| `test_gms_permanent_failure_wording_unchanged`(회귀) | 업스트림 401 고정 | `{"detail": "embedding upstream rejected the request"}` |

민감 값 비노출은 기존 계약 테스트(`test_error_response_exposes_no_configured_values`,
`test_db_error_response_exposes_no_connection_details`)가 이미 지키고 있고, 이번에
추가한 네 문구는 전부 값이 없는 상수라 별도 테스트를 더하지 않았다 — 기존 두 테스트가
그대로 통과하는 것으로 충분하다.

### 4.2 RED → GREEN

`app/main.py`를 고치기 전 신규 DB 축 테스트 2건을 먼저 돌려 **RED 확인** — 두 테스트
모두 `embedding upstream ...`가 나와 실패했다(GMS 회귀 테스트 2건은 이미 GREEN이었다,
현재 동작이 맞았으므로). 핸들러에 `isinstance` 분기를 넣은 뒤 4건 모두 GREEN.

```
FAILED test_db_transient_failure_uses_database_wording  (embedding upstream unavailable != database unavailable)
FAILED test_db_permanent_failure_uses_database_wording  (embedding upstream rejected the request != database rejected the request)
```

### 4.3 Regression

```
ruff check .                    All checks passed!
python -m compileall app tools  오류 없음
pytest (전체, Testcontainers 포함)  425 passed
line coverage    99.83% (1163/1165)
branch coverage  98.99% (196/198)   둘 다 게이트(80%) 통과
```

`app/main.py`는 100% 라인·브랜치 커버리지를 유지한다. 미달 두 줄(`embedding_service.py`
83행, `keyword_service.py` 248행)은 이 티켓 이전부터 있던 미달이며 이번 변경과 무관하다.

실서버 대조는 별도로 하지 않았다 — `-221`이 그 방식(전용 pgvector 컨테이너를
`docker stop`)을 이미 검증했고, 이번 변경은 그 경로의 **문구**만 바꿔 계약 테스트가
이미 실제 `asyncpg` 실패를 통해 관통하고 있다. 상태 코드·재시도·분류 로직에 손대지
않았으므로 별도 실서버 대조가 새로 확인해 줄 것이 없다고 판단했다.

## 5. 남긴 것

- **`static/05` 반영하지 않음** — §3의 근거. 후속 필요 없음.
- **Context 처리 경로(`/context/process`)는 영향 없음** — `-221`이 확인한 대로 202를
  먼저 반환하는 백그라운드 구조라 이 핸들러들을 거치지 않는다.
- **`53100`(디스크 가득 참) 분류, `InterfaceError` 미분류 등 `-221`의 판단은
  재논의하지 않았다** — 티켓 범위 밖.

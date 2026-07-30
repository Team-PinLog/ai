# 외부 API 재시도·오류 분류 정합화 (S15P11A705-121)

상태: 완료 · 유형: 구현 · 근거 명세: [failure-recovery.md](../spec/failure-recovery.md) §2.1 · §2.2 · §3.1 · §3.2

`docs/implements/2026-07-28-s1-implementation-recovery.md` §5가 기록한 구현 결함 A-1~A-4·F-1~F-3 중
Circuit Breaker(A-6)와 타임아웃 값 재산정(A-5)을 제외한 전부를 수정했다. 문서화 누락이 아니라
**두 클라이언트가 상태 코드를 정반대로 분류하던** 구현 결함이다.

## 1. 무엇이 반대였나

| | spec | 수정 전 구현 | 결과 |
|---|---|---|---|
| `429` | Transient (§2.1) | `>= 500`만 Transient → 나머지 non-200 전부 Permanent | rate limit 한 번에 해당 Context가 **영구 실패** |
| LLM `400`·`401`·`403` | Permanent (§2.2) | **모든** non-200을 Transient | 인증 실패가 재스캔 주기(5분)마다 GMS 호출을 생성 |

두 클라이언트가 각자 상태 코드 표를 들고 있었던 것이 원인이다. 그래서 매핑을
`app/core/errors.py`의 `classify_http_status` 한 곳으로 모았다 — spec §2가 "`errors.py`에서
분류한다"고 지목한 지점이다. `429`를 `>= 500`보다 **먼저** 판정해야 4xx로 떨어지지 않는다.

## 2. 재시도 (§3.1)

`app/client/retry.py`의 `RetryPolicy` + `call_with_retry`. 총 3회 시도(최초 1회 + 재시도 2회),
지수 백오프 `0.5s → 1.0s`(상한 4.0s)에 full jitter.

**재시도 대상은 `TransientError` 하나다.** §3.1의 대상 목록(타임아웃·429·5xx·연결 실패)이 §2.1의
Transient 집합과 같고 비대상 목록이 곧 `PermanentError`이므로, 재시도 여부를 판정하는 두 번째
상태 코드 표를 만들지 않았다. 표가 둘이면 갈라지고, 실제로 갈라진 것이 위 §1이다.

재시도 단위는 **API 호출 1회**다 — Embedding은 배치 1건 단위로 재시도해 성공한 앞 배치를 다시
보내지 않는다(`test_embedding_retry_does_not_resend_successful_batch`).

**백오프 값을 `Settings`(환경변수)로 열지 않았다.** §3.2의 상한("두 호출의 타임아웃 합 + 재시도
시간 < PROCESSING 만료 600s")에 묶인 값이라 env로 열면 그 상한이 배포마다 달라진다. 현재 최악값은
`3 × (60 + 90) + 2 × 1.5 = 453s < 600s`이고 이 부등식을 테스트가 지킨다
(`test_retry_budget_fits_processing_expiry`). 대신 `RetryPolicy`를 생성자 인자로 받아 **테스트가
sleep·jitter를 주입**한다 — 실제로 잠드는 테스트를 만들지 않기 위한 설계다.

## 3. 구조화 출력 위반 (§2.2 "재시도 후에도")

`SchemaViolationError(TransientError)`를 도입했다. 하위 타입인 것이 설계의 핵심이다.

- 재시도 중에는 Transient로 동작한다 — 같은 요청에 대한 LLM 출력은 결정론적이지 않으므로
  재요청이 성공할 여지가 있다(`test_llm_schema_violation_recovers_within_retry`).
- **소진되면 `judge`가 `PermanentError`로 승격한다.** 그래서 service가 보는 분류는 여전히 두
  종류이고 §2의 체계가 깨지지 않는다. 하위 타입인 채로 새어 나가면 `except TransientError`가
  먼저 잡아 무한 재판정이 된다 — 그것을 `test_schema_violation_does_not_escape_as_transient`가 막는다.

Embedding 응답 형식 위반은 이 타입을 쓰지 않고 곧바로 Permanent다. 프로바이더가 같은 요청에 같은
형식으로 답하므로 재시도가 무의미하다.

## 4. service 결선 — 결함 3의 나머지 절반

`keyword_service.run`은 `judge` 호출을 `except TransientError`로만 감싸고 있었고 `PermanentError`
핸들러가 없었다. 호출 경로 위쪽도 무방비였다(`context_processing`에 광범위 except 없음,
`context.py`는 `BackgroundTasks.add_task`로 넘김).

따라서 `llm_client`만 고치면 401이 **BackgroundTasks까지 새어 트레이스백만 남기고 단계는
PROCESSING에 머문다** — 만료 후 재스캔이 같은 호출을 반복하므로, 고치려던 무한 재시도가 경로만
바꿔 그대로 남는다. `PermanentError → _fail()` 결선을 추가했다. 되돌려서 실패를 확인했다(§6).

`embedding_service`는 이 결선을 이미 갖고 있었다. 비대칭이었던 쪽만 맞췄고 상태 전이 규칙은
건드리지 않았다.

로그 레벨도 명세에 맞췄다 — 일시 오류 `WARN` + `context_id`·`stage`·원인(§2.1), 영구 오류
`ERROR`(§2.2). 둘 다 `INFO`/`WARNING`이어서 일시 장애가 묻히고 배포 설정 문제가 알림에 오르지
않았다.

## 5. 테스트 — 결함 5(근본 원인) 해소

`tests/fakes.py`의 `raise_exc`가 **어느 테스트에서도 쓰이지 않아** Transient/Permanent 파이프라인
경로가 한 번도 실행된 적이 없었다. 그것이 위 §1의 두 오분류가 잡히지 않은 직접 원인이다.

| 파일 | 계층 | 추가 |
|---|---|---|
| `tests/test_unit.py` | 순수 단위 | 상태 코드 분류 표 대조 · 백오프 수열·jitter 범위 · §3.2 예산 부등식 |
| `tests/test_client_retry.py` (신설) | client HTTP | 상태 코드→오류 타입 · 재시도 횟수·간격 · 스키마 위반 승격 · 배치 재전송 안 함 |
| `tests/test_pipeline.py` | 파이프라인 | `raise_exc`로 4경로 + CANCELLED 경합 2건 |

파이프라인 단언은 **transient 동안 `PROCESSING` 유지**(다른 단계 불변, `retry_count` 불변, 부분
결과 없음)와 **permanent 시 해당 단계만 `FAILED`**(COMPLETED 단계 보존)다.

`test_client_retry.py`는 `integration-tests.md` §5의 4계층 밖이다. §4.2("HTTP 레벨 목이 아니라
인터페이스 레벨 Fake")는 *파이프라인이 client를 무엇으로 대체하는가*의 규칙이고, 여기서 검증하는
것은 그 대체물이 아니라 **실제 client 자신의 HTTP 계층**이다. 인터페이스 Fake로는
`_embed_batch`가 429를 어떻게 분류하는지 볼 수 없다 — 그 공백이 F-3이었다. `httpx.MockTransport`를
쓰며 DB·Docker·네트워크가 필요 없고, 주입한 sleep이 실제로 잠들지 않는다. 이 예외는
`tests/README.md`에 기록했다. **`docs/spec/`은 수정하지 않았다.**

## 6. 검증

```
ruff check .                        exit 0
pytest                              131 passed (착수 시 baseline 74 → +57)
```

Docker 29.6.1 가동 상태에서 Testcontainers 실 pgvector 포함 전량 실행. 미실행 범위 없음.

**결함을 실제로 잡는지 확인**: `app/service/keyword_service.py`의 `PermanentError` 핸들러만
되돌려(`git stash`) `test_judge_permanent_fails_keyword_stage`를 돌리면
`app.core.errors.PermanentError: llm error: 401`이 파이프라인 밖으로 새며 실패한다. 예측한
경로와 같다.

## 7. 남은 것

- **Circuit Breaker** (spec §2.2 말미) — 티켓 제외 범위. MVP 밖으로 유예. 인증 실패·모델명 오류
  같은 전 서비스 영향 오류를 개별 Context의 FAILED로 누적시키는 문제는 그대로 남는다.
- **연결·읽기 타임아웃 분리와 60/90 재산정** (§3.2) — 티켓 제외 범위(A-5). 현재는 단일 total이며,
  재시도 도입으로 최악값이 150s → 453s가 되었다. 600s 상한 안이지만 여유가 줄었고 그 부등식은
  테스트가 지킨다.
- `docs/WORKLOG.md` 갱신 — 계약이 이 파일을 금지 범위로 지정해 이 PR에서 건드리지 않았다.
  CONTRIBUTING §구현과 문서는 작업 이력 보존을 요구하므로 별도 반영이 필요하다.

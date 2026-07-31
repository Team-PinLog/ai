# 판정 LLM 벤더 폴백 (S15P11A705-175)

상태: 완료 · 근거 계약: [failure-recovery.md §3.4](../spec/failure-recovery.md) ·
관련: [S15P11A705-121 재시도·오류 분류](2026-07-30-retry-and-error-classification.md),
[S15P11A705-174 실데이터 E2E](2026-07-30-real-data-e2e.md)

판정 LLM 호출이 Gemini 한 경로에 묶여 있던 것을 벤더 어댑터 구조로 풀고, 일시적 오류일 때
다음 벤더로 넘어가게 했다. 도메인 코드·DB 스키마 변경은 없다.

## 1. 왜 — 429는 게이트웨이 전역이 아니라 Gemini 경로에만 있었다

2026-07-30 실측이다. 같은 시각·같은 GMS 키·같은 판정 작업(Context 5건 × 모델당 12~15회)을
던졌다. 도구는 `tools/keyword_eval/probe_vendors.py`.

| 모델 | 성공률 | 평균 응답 | prompt | output | 현행과 일치도 |
|---|---|---|---|---|---|
| gpt-4o-mini | **100%** | **0.91s** | 821 | 15 | 0.83 |
| gpt-4.1-mini | **100%** | 1.37s | 821 | 13 | 0.93 |
| gpt-4.1-nano | **100%** | 0.80s | 821 | 18 | 0.53 |
| claude-haiku-4-5-20251001 | **100%** | 1.60s | 1973 | 57 | 0.83 |
| gemini-2.5-flash (현행) | 92% | 1.23s | 732 | 21 | 기준 |
| gemini-2.5-flash-lite | **58%** | 1.09s | 732 | 48 | 0.90 |

**OpenAI·Anthropic 경로는 한 번도 막히지 않았다.** 임베딩(OpenAI 호환 경로) 49회가 안 막힌
것과 같은 그림이다 — 쿼터가 게이트웨이 전역이 아니라 **프로바이더 경로별로** 걸린다.

429가 나면 `keyword_status`가 `PROCESSING`에 남고 `AiProcessClient`가 실패를 삼킨다. 화면에는
Context가 정상 생성된 것으로 보이는데 키워드도 검색도 안 된다. 재스캔(`S15P11A705-159`)이 5분
뒤 복구하지만 시연 중 5분은 없는 시간과 같다.

## 2. 확정한 폴백 순서

| 순위 | 벤더:모델 | 근거 |
|---|---|---|
| 1 | `openai:gpt-4o-mini` | 100% · 0.91s로 가장 빠르다 |
| 2 | `gemini:gemini-2.5-flash` | 현행. 결과 기준선이므로 남긴다 |
| 3 | `anthropic:claude-haiku-4-5-20251001` | 프로바이더가 셋째라 동시 장애 가능성이 가장 낮다 |

기각한 후보:

- `gpt-4.1-nano` — 일치도 0.53. 빠르지만 다른 답을 낸다.
- `gemini-2.5-flash-lite` — 58%. 가장 많이 막혔다.
- `gpt-4.1-mini` — 일치도 1위(0.93)지만 응답 1.37s이고 최대 4.67s까지 튄 관측이 있다. 그 차이는
  판정 비결정성 범위 안이라 속도를 택했다.

## 3. 구조

| 파일 | 역할 |
|---|---|
| `app/client/vendors.py` (신설) | 벤더별 요청 생성·응답 봉투 해석. 어댑터 레지스트리와 체인 해석 |
| `app/client/llm_client.py` | 어느 벤더로 부를지 고르고, 오류를 분류하고, 선택 결과를 `JudgeResult`로 옮긴다 |
| `app/core/config.py` | `PINLOG_JUDGE_CHAIN` — 형식 검증과 기동 시 fail-fast |
| `app/client/_usage.py` | 벤더별 토큰 필드 추출 + 행에 `vendor`·`model` |
| `app/schema/llm.py` | `JudgeResult.model` — **실제로 답한** 모델 |
| `app/service/keyword_service.py` | `model_profile`에 답한 모델을 저장 |

경로와 인증이 벤더마다 다르다. `root`는 `GMS_BASE_URL`에서 `/gmsapi/` 앞을 잘라 만들고,
키는 셋 다 같은 `GMS_API_KEY`다 — 헤더 이름만 다르다.

```text
OpenAI      {root}/api.openai.com/v1/chat/completions
            Authorization: Bearer <key>        · response_format json_schema(strict)
Gemini      {root}/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
            x-goog-api-key: <key>              · responseSchema + thinkingBudget=0
Anthropic   {root}/api.anthropic.com/v1/messages
            x-api-key + anthropic-version      · tools + tool_choice(강제 호출)
```

작동 확인된 형태는 `probe_vendors.py`에서 옮겼다. **스키마만은 그대로가 아니다** — 프로브는
`keywordId` 하나만 요구하는 축약본이었고(속도·일치도 측정용), 운영은 `confidence`와
`unmatchedConcepts`까지 받아야 한다. OpenAI strict 모드는 모든 객체에
`additionalProperties: false`와 전 property `required`를 요구하므로 Gemini 스키마를 그대로
재사용할 수 없다. 빠지면 400이고, 400은 영구 오류라 **폴백 없이 판정이 죽는다.**

## 4. 판단 — 시도 예산을 체인 길이에 곱하지 않는다

`RetryPolicy.attempts`(기본 3)를 **판정 호출 1건의 총 HTTP 시도 횟수**로 두고, n번째 시도가
체인의 n번째 벤더를 쓴다. 체인이 짧으면 마지막 벤더를 반복한다.

벤더마다 3회씩 재시도하는 안을 기각한 이유는 §3.2의 상한이다 — "두 호출의 타임아웃 합 +
재시도 시간 < PROCESSING 만료 600s". 벤더별 재시도면 최악이 3벤더 × 3시도 × 90s = 810s로
만료를 넘고, 그러면 재스캔이 아직 살아 있는 판정을 중복 실행해 비용이 배가 된다. 이 설계에서는
최악이 3시도 × 90s = 270s로 폴백 이전과 같고, `test_retry_budget_fits_processing_expiry`가
그 상한을 그대로 지킨다.

부수 효과가 오히려 본질에 가깝다 — 같은 벤더에 백오프를 걸고 다시 던지는 것보다 **막히지 않은
다른 경로로 즉시 넘어가는 편이 성공 확률이 높다.** 429는 경로별로 걸리기 때문이다(§1).

그리고 **체인을 벤더 하나로 줄이면 시도 배분이 그 벤더로 모여 폴백 이전과 정확히 같아진다.**
롤백이 설정 한 줄인 것이 이 설계의 결과다.

## 5. 넘어가는 조건과 넘어가지 않는 조건

`TransientError`(429·5xx·타임아웃·연결 실패·구조화 출력 위반)만 다음 벤더로 넘어간다.
`PermanentError`(400·401·403)는 넘어가지 않는다 — 키·설정 문제는 다른 벤더에서도 같은 답이고,
넘어가면 GMS 호출만 3배가 된다(`S15P11A705-121` 결함 3의 재발 형태).

구조화 출력 위반을 폴백 사유에 넣은 이유는 벤더마다 구조화 출력 방식이 다르기 때문이다 —
한쪽이 절단·안전 차단으로 깨져도 다른 쪽은 성공할 수 있다. 소진 후에는 기존과 같이 영구
오류로 승격한다(§2.2).

체인이 소진되면 기존과 같은 분류로 끝난다. 상태는 PROCESSING에 남고 재스캔이 회수한다 —
재스캔이 집을 수 있는 상태를 유지하는 것이 완료 조건이었다.

## 6. 어느 벤더가 답했는지

두 곳에 남는다.

- **토큰 로그**(`PINLOG_TOKEN_LOG` JSONL) — `vendor`·`model` 필드. 벤더마다 토큰 필드 이름이
  달라(`usageMetadata` camelCase / `usage.prompt_tokens` / `usage.input_tokens`) 추출도 벤더별로
  갈랐다. Anthropic은 합계를 주지 않아 입력+출력을 더해 남긴다.
- **`ai.context_keyword_analysis.model_profile`** — 설정 1순위가 아니라 **답한 모델**이다.
  `model_profile`의 용도가 "어떤 모델의 판단이었는지 구분"(keyword-preset.md §5.2)이므로,
  폴백이 생긴 뒤에 설정값을 그대로 쓰면 그 구분이 거짓이 된다. 티켓의 명시 범위는 토큰 로그
  하나였지만, 폴백을 넣는 순간 이 필드가 조용히 틀려지므로 같은 PR에서 고쳤다.

## 7. 설정

```bash
PINLOG_JUDGE_CHAIN=openai:gpt-4o-mini,gemini:gemini-2.5-flash,anthropic:claude-haiku-4-5-20251001
```

`PINLOG_JUDGE_MODEL`을 대체했다. 모델 하나로는 폴백 순서를 표현할 수 없고, 벤더 이름 없이는
어느 경로·어느 인증 헤더로 부를지 알 수 없다. **옛 키는 이제 무시된다** — 배포 설정에는
주입되지 않았고(봉인 대상은 `GMS_API_KEY`·`GMS_BASE_URL`·`INTERNAL_SHARED_SECRET` 셋뿐),
`.env.example`과 테스트 픽스처에서 함께 제거했다. 로컬 `.env`에 남아 있으면 조용히 무시되므로
이 문서와 `.env.example`이 그 안내를 겸한다.

정본은 코드(`app/core/config.py`)에 두고 주입은 덮어쓰기다(P45). 형식 오류·빈 체인은 기동에서
막는다 — 판정 경로는 첫 Context 요청까지 실행되지 않으므로, 미루면 서버는 정상으로 보이는데
Keyword만 통째로 생성되지 않는 비대칭 장애가 된다(`GMS_BASE_URL` 세그먼트 누락과 같은 종류).
형식은 config가 보고, **지원 벤더인지는 어댑터 레지스트리만 알 수 있어** 클라이언트 생성이
본다(둘 다 lifespan startup이다).

## 8. 검증

| 항목 | 명령 | 결과 |
|---|---|---|
| lint | `ruff check .` | exit 0 |
| compile | `python -m compileall app tools` | exit 0 |
| 테스트 | `pytest -q` | **239 passed** (기존 189 → +50) |
| coverage 게이트 | `python tools/check_coverage_gate.py` | line 99.78% (891/893) / branch 98.46% (128/130) — 둘 다 통과 |

RED 드릴 2회를 실제로 관측했다.

| 드릴 | 주입한 결함 | 관측 |
|---|---|---|
| 1 | `_call_for`가 항상 1순위를 반환(폴백 무력화) | 6건 실패 — 429 폴백, 시도별 벤더 순서, 오류 메시지의 벤더, 스키마 위반 폴백, 전송 실패 폴백, 토큰 로그 |
| 2 | 재시도 드라이버가 `PermanentError`도 다음 벤더로 넘김 | 14건 실패 — 이 PR 신규 3건(`401` 폴백 금지, `400`·`403` 폴백 금지)과 기존 재시도 계약 11건이 함께 무너진다. 폴백이 §2.2 분류를 재정의하지 않았다는 증거다 |

**실 GMS 호출은 하지 않았다.** 이 PR의 테스트는 `httpx.MockTransport`로 세 벤더의 응답 봉투를
직접 만든다(tests/README.md — 실호출을 CI에 넣지 않는다). §1의 실측은 이 PR 이전에
`probe_vendors.py`로 수행한 것이고, 어댑터가 실제 GMS에서 왕복하는지는
`python -m app.smoke.gms_roundtrip`으로 배포 절차에서 확인한다.

## 9. 남는 위험·미검증

- **표본은 Context 5건 · 하루 오후 한 시점이다.** 시간대가 바뀌면 성공률 분포가 달라질 수 있다.
  1순위가 바뀌어야 할 상황이 오면 설정 한 줄이며, 그것이 이 구조의 목적이다.
- **일치도 0.83~0.93의 차이에는 판정 자체의 비결정성이 섞여 있다.** 현행 모델도 같은 Context를
  반복하면 흔들린다. 벤더가 바뀌면 Keyword가 바뀔 수 있고 그것은 사용자에게 보이는 값이다.
- **Anthropic은 prompt가 1973으로 2.4배다.** 토큰 기준 과금이면 3순위가 비싸다. GMS가
  프로바이더별 과금을 어떻게 처리하는지 모르므로 확인 전에는 판단하지 않는다
  ([cost-estimate.md](../spec/cost-estimate.md) §3 주석과 같은 이유).
- **스모크는 체인 전체를 증명하지 않는다.** 1순위가 429면 2순위로 넘어가 통과하므로, 통과가
  "세 벤더가 모두 살아 있다"를 뜻하지 않는다. 3순위가 죽어 있는데 모르는 상태가 가능하다.
  벤더별 가용성은 `probe_vendors.py`로 수동 확인한다 — 스모크에서 벤더마다 실호출하면 배포
  게이트가 GMS 쿼터에 3배로 묶인다. 상시 관측은 `S15P11A705-96` 범위다.
- **1순위 모델명에 오타가 있으면 400/404 → 영구 오류로 폴백 없이 죽는다.** 폴백 이전과 같은
  실패 양상이지만, 체인이 길어질수록 오타 지점도 늘어난다. 기동 시 검증은 형식과 벤더 이름까지이며
  **모델명이 실제로 존재하는지는 실호출만이 안다**(스모크의 몫이다).
- 벤더별 프롬프트 튜닝은 하지 않았다. 같은 프롬프트로 일치도 0.83~0.93이 나왔고, 별건이다.

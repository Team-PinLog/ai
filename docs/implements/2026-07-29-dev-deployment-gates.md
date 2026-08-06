# dev 배포 게이트 3종 — `/ready` 프로브, `GMS_BASE_URL` 기동 검증, GMS 양방향 스모크를 구현했다

- **상태**: 완료
- **날짜**: 2026-07-29
- **관련 PR**: [ai#33](https://github.com/Team-PinLog/ai/pull/33)
- **근거 계약**: [ai#32](https://github.com/Team-PinLog/ai/pull/32) `docs/S15P11A705-96-dev-deployment-contract.md` — 인프라(김세민) 요청 §2·§3과 코멘트 합의(2026-07-29)
- **Jira**: [S15P11A705-96](https://ssafy.atlassian.net/browse/S15P11A705-96)

## 무엇을 만들었나

인프라가 dev 배포의 activation 조건으로 요청한 게이트 3종이다. 셋은 같은 결함 하나를 서로 다른 시점에서 막는다. 그 결함이란 "틀린 설정으로도 서버가 정상 기동하고, 첫 실사용 요청에서야 실패한다"는 것이다([ai#32 리뷰 ①·②](https://github.com/Team-PinLog/ai/pull/32)).

| 시점 | 게이트 | 잡는 것 |
|---|---|---|
| 기동 | `Settings._gms_base_url_shape` | `GMS_BASE_URL`의 `/gmsapi/` 세그먼트 누락 |
| 배포 직후 | `python -m app.smoke.gms_roundtrip` | 인증 오류 · 네트워크 도달 실패 · 모델 미존재 |
| 운영 중 | `GET /ready` | 기동 후 끊긴 DB · 비어 있는 Preset 캐시 |

## 1. `GET /ready` (`app/api/probe.py`)

```
DB      커넥션 획득 후 SELECT 1
preset  현재 Embedding Profile 기준 캐시 ≥ 1건
성공    200 {"status": "ready"}
실패    503 {"status": "not_ready"}
```

설계 판단 4건을 남긴다.

- **GMS 를 호출하지 않는다.** 준비 판정에 외부 게이트웨이의 가용성을 섞으면, 자기 책임 밖의 장애 때문에 인스턴스가 트래픽에서 빠지게 된다. GMS 도달성은 배포 시점의 스모크가 따로 증명한다(계약 §2 의 요청과 일치한다).
- **Profile 조건을 재조회하지 않는다.** 캐시는 lifespan 이 `settings.embedding_profile` 로 조회한 행만 담는다(`main.py`). 따라서 캐시 건수가 1건 이상이라는 것이 곧 "현재 Profile 기준 1건 이상"이다. 별도 쿼리를 두는 것은 같은 사실을 두 번 묻는 일이다.
- **무인증으로 연다.** `SharedSecretMiddleware` 는 `/internal/` 경로만 가로채므로 `/ready` 는 헤더 없이 호출된다. 프로브가 시크릿을 들고 다니지 않게 하려는 계약상의 의도다.
- **응답에 내부 값을 싣지 않는다.** 무인증으로 노출되는 경로이므로 `{"status": ...}` 한 필드만 싣는다. 실패 시 로그에도 예외 타입 이름만 남긴다. asyncpg 예외 메시지에는 DSN 이 섞여 들어올 수 있기 때문이다.

`GET /health` 는 정적 `{"status":"ok"}` 를 그대로 유지했다(liveness·startup 전용). 합의사항이므로 회귀 테스트(`test_health_stays_ok_while_not_ready`)로 고정했다.

## 2. `GMS_BASE_URL` 형식 fail-fast (`app/core/config.py`)

한 변수를 두 클라이언트가 다르게 소비한다. 임베딩 클라이언트는 `{URL}/embeddings` 를 그대로 붙이고, 판정 클라이언트는 `URL.split("/gmsapi/")[0] + "/gmsapi"` 로 root 를 파생시킨다. 따라서 URL 에 `/gmsapi/` 세그먼트가 빠지면 임베딩은 정상 동작하는데 judge 만 조용히 실패한다. `_gms_base_url_shape` model_validator 가 이 상태를 기동 시점에 막는다.

예외 타입은 `SettingsError`(RuntimeError)를 쓰고 `ValueError` 를 쓰지 않는다. pydantic 은 `ValueError`/`AssertionError` 만 가로채 `ValidationError` 로 감싸는데, 그때 `input_value` 를 메시지에 실어 넣기 때문이다. 실측 결과는 다음과 같다(pydantic 2.13.4).

| 검증 방식 | 메시지에 실리는 것 |
|---|---|
| `field_validator` + `ValueError` | `input_value='https://…'` — endpoint 전체가 노출된다 |
| `model_validator(after)` + `ValueError` | `input_value={'secret': 'SENTINEL-SECR…` — 원시 입력 dict 의 앞부분이 노출된다 |
| `model_validator(after)` + `SettingsError` | 우리가 쓴 문장만 실린다 |

기동 실패 메시지는 배포 파이프라인 로그에 남으므로 세 번째 방식을 택했다. `test_gms_base_url_error_carries_no_values` 가 이 성질을 고정한다.

> **범위 밖 관찰**: 기존 `_profile_consistency` 는 여전히 `ValueError` 경로다. Profile 불일치로 기동이 깨지면 같은 방식으로 원시 입력 dict 앞부분이 로그에 남는다. 이번 티켓 범위가 아니라 손대지 않았다. 별도 티켓 후보다.

검증 대상은 `/gmsapi/` 세그먼트 포함 여부 하나다. scheme·host 형식은 검사하지 않는다. 그쪽 오류는 스모크가 실제 호출로 잡는 편이 확실하고, 검증 규칙을 늘리면 정상 값을 잘못 막을 위험만 커지기 때문이다.

## 3. GMS 양방향 스모크 (`app/smoke/gms_roundtrip.py`)

```bash
python -m app.smoke.gms_roundtrip
```

설계 판단 4건을 남긴다.

- **한쪽이 실패해도 나머지를 건너뛰지 않는다.** 이 스모크가 존재하는 이유가 임베딩·판정의 비대칭 장애다. 한 번의 실행으로 어느 쪽이 실패했는지 알아야 하므로, 종료 코드는 마지막에 한 번만 판정한다.
- **DB 를 건드리지 않는다.** 판정 후보를 모듈 안의 고정 리터럴로 두어 Preset 적재 여부와 무관하게 동작한다. 읽기조차 하지 않으므로 부트스트랩 Job 전후 어느 시점에 돌려도 된다.
- **판정 내용을 단언하지 않는다.** 판정은 비결정적이므로(`spec/keyword-preset.md`) 선택 결과가 비어 있어도 성공이다. 증명 대상은 인증·경로·모델이 살아 있는지이지 판정 품질이 아니다.
- **출력에 값을 싣지 않는다.** 검사 이름과 `ok`/`failed (예외타입)` 만 출력한다. 클라이언트 예외 메시지에는 응답 본문 일부(`resp.text[:200]`)와 요청 URL 이 섞여 오므로 예외 타입 이름으로 바꿔 출력한다. 추가로 `httpx`·`httpcore` 로거 레벨을 `CRITICAL` 로 올렸다. httpx 는 요청마다 INFO 레벨로 전체 URL 을 남기는데, 이 명령의 출력은 배포 로그에 남기 때문이다. `configure_logging()` 을 부르지 않는 이유도 같다. root 로거를 INFO 로 열면 그 로그가 켜진다.

`get_settings()` 가 단일 Settings 를 강제하므로, 실행에는 서버와 동일한 env 전체가 필요하다. 부트스트랩 Job 과 같은 알려진 제약이며(ai#32 §4) 그대로 둔다.

## 검증

기준 커밋에서의 실측이다.

| 항목 | 명령 | 결과 |
|---|---|---|
| lint | `ruff check .` | exit 0 |
| compile | `python -m compileall app tools` | exit 0 |
| 테스트 | `pytest -q` | **66 passed** (기존 52 → +14) |
| `/ready` 실기동 | `uvicorn app.main:app --port 8011` → `curl /ready` | `200 {"status":"ready"}` (실 pgvector·preset 27건) |
| `/health` 실기동 | `curl /health` | `200 {"status":"ok"}` — 형태 불변 |
| 스모크 정상 | `python -m app.smoke.gms_roundtrip` (실 GMS) | `embedding: ok` / `judge: ok` / exit **0** |
| 스모크 비대칭 실패 | 위 명령 + `PINLOG_JUDGE_MODEL=<미존재 모델>` | `embedding: ok` / `judge: failed (TransientError)` / exit **1** |
| URL fail-fast | `GMS_BASE_URL=<세그먼트 없는 값> python -c "import app.main"` | `SettingsError` 로 기동 중단. 메시지에 값 없음 |

추가된 테스트는 14개다.

| 파일 | 수 | 내용 |
|---|---|---|
| `tests/test_api.py` | 6 | `/ready` 200·503(캐시 0건)·503(DB 끊김)·무인증·값 미노출·`/health` 불변 회귀 |
| `tests/test_unit.py` | 8 | `GMS_BASE_URL` 거부 2·수용 1·값 미노출 1 · 스모크 집계 3·출력 1 |

스모크의 실제 호출은 단위 테스트로 덮지 않았다. 외부 의존이므로 `_CHECKS` 를 스텁으로 교체해 집계·종료 코드·값 미노출 규약만 검증하고, 실제 GMS 왕복은 위 표의 수동 실측으로 대신했다. 실제 호출을 CI 에 넣으면 GMS 가용성이 CI 성패에 들어오기 때문이다.

## 인프라에 전달할 것

1. 이 PR 병합 후의 immutable source SHA 와 image digest (ai#32 §5 — AI CI 가 main push 에서 1회 build/publish 한다)
2. readiness probe 경로를 `/ready` 로, startup/liveness 는 `/health` 로 배선한다
3. activation 전에 컨테이너에서 `python -m app.smoke.gms_roundtrip` 을 실행해 exit 0 을 확인한다

## 남은 것

- `_profile_consistency` 의 `ValueError` 경로 값 노출(§2 의 범위 밖 관찰) — 별도 티켓으로 다룬다.
- 외부 API retry/error classification — `S15P11A705-121`. dev 배포의 blocker 는 아니다(ai#32 합의).
- `llm_client` 의 `/gmsapi/` 리터럴과 `config.GMS_PATH_SEGMENT` 가 각자 리터럴로 존재한다. 통합은 client 계층 변경이라 이번 범위 밖이다.

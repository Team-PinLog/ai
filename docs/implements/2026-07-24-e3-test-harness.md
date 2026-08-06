# E3 통합 테스트 — 하네스와 저수준 27케이스·파이프라인 20시나리오를 구현하고 테스트 인프라를 정비했다

- **상태**: 완료 (E3-PR1·PR2)
- **날짜**: 2026-07-24 (PR2 는 2026-07-27)
- **관련 PR**: [ai#14](https://github.com/Team-PinLog/ai/pull/14)(E3-PR1), [ai#16](https://github.com/Team-PinLog/ai/pull/16)(핫픽스), [ai#18](https://github.com/Team-PinLog/ai/pull/18)(E3-PR2 파이프라인 20)
- **근거 계약**: [spec/integration-tests.md](../spec/integration-tests.md) (§16 검증 시나리오)

## 무엇을 만들었나

계약 §16 의 검증 시나리오를 자동 pytest 스위트로 옮기는 작업이다. E3-PR1 에서 하네스와 저수준 3계층(단위·저장소·API) 테스트를 구현했다. 파이프라인 시나리오 20개(`test_pipeline.py`, 계약 §3)는 E3-PR2([ai#18](https://github.com/Team-PinLog/ai/pull/18))에서 구현을 완료했다. 시나리오 6과 18이 한 테스트 함수를 공유하므로 함수 수는 19개다(19함수/20시나리오). 챗봇/GraphRAG 스택의 합류에 대비한 환경 통일(Python 3.12·lock 파일)도 함께 반영했다. 프로덕션 코드(`app/`)의 변경은 `db.py` 의 search_path 보정 1건뿐이다(T21).

## 하네스 (`tests/`)

- **`conftest.py`** — 세션 스코프의 `PostgresContainer(PGVECTOR_IMAGE)`를 띄운다. 이미지는 현재 `pgvector/pgvector:0.8.5-pg16@sha256:1d53…` 로, 운영 환경과 back 레포 `compose.yaml` 의 이미지와 digest 까지 일치한다(최초에는 `0.8.1-pg16` 이었고 `S15P11A705-122` 에서 정합화했다. 경위는 아래 「결정」 참조). 컨테이너에 `schema/ai_snapshot.sql`(back V1/V100/V101 에서 파생)을 적용한 뒤 asyncpg 풀을 연다. 테스트 간 격리는 TRUNCATE 로 한다. 롤백 격리를 쓰지 않는 이유는 동시성 테스트가 여러 커넥션을 쓰기 때문이다. `settings` fixture 가 Embedding Profile 을 주입하며, 테스트 코드에 Profile 문자열 리터럴을 쓰는 것은 금지다.
- **`fakes.py`** — `FakeEmbeddingClient`/`FakeLLMClient`. 벡터는 sha256 기반으로 결정론적으로 생성한다. 무작위 벡터를 쓰면 유사도 순서 단언이 실행마다 흔들리기 때문이다. 호출 횟수를 기록한다. 여러 시나리오의 핵심 단언이 "호출하지 않았다" 또는 "정확히 한 번 호출했다"이기 때문이다. `on_call` 훅으로 모델 호출과 저장 사이의 시간 창을 결정론적으로 재현한다. sleep 으로 타이밍을 맞추는 방식은 금지다.
- **`builders.py`** — `make_state`/`make_embedding`/`make_preset`. `embedding_profile`·`is_deleted`·두 status 컬럼을 항상 명시한다. 본문 버전 인자는 두지 않았다. 버전은 설계에서 제거된 개념이라 테스트 빌더가 되살리면 안 되기 때문이다. 본문 수정 시나리오는 `context_id` 가 다른 두 State 로 표현한다.
- **`schema/ai_snapshot.sql`** — 테스트 전용 스키마 스냅샷. `ai` 스키마만 담는다(ai 테이블은 core 로의 FK 가 없다). 파일 헤더에 back 파생 출처와 갱신 누락 위험을 명시했다.

## 저수준 27케이스 (계층 배분)

| 계층 | 파일 | 검증 |
|---|---|---|
| 단위(DB 없음) | `test_unit.py` | 오류 분류, 후보 TOP-K(`_topk`), LLM 응답 매핑·폐기(`_map` — 후보 밖 keywordId 와 범위 밖 confidence 의 폐기, 중복 접기), Profile 검증(config validator) |
| 저장소(실제 DB) | `test_repo.py` | 조건부 전이의 rowcount(PENDING·만료 PROCESSING 만 전이), `is_deleted` 를 제외하는 UPSERT 회귀, delete-insert, 검색 DISTINCT ON(대표 contextId 선택) |
| API(실제 DB, Fake 주입) | `test_api.py` | 202 접수, 검색 응답 형식(contextId 포함), Profile 불일치 422, 시크릿 없음 401 |

## 인프라

- **`Dockerfile`** — `python:3.12-slim` 기반. `requirements.lock` 을 설치하고, 비루트 사용자로 `uvicorn app.main:app` 을 실행한다.
- **`ai-ci.yml` 정비** — 새로 만든 것이 아니라 기존 워크플로를 수정했다. lock 설치로 전환하고, PR 제목의 Jira 키 검증 스텝을 추가했다(squash 병합이므로 PR 제목이 최종 커밋 메시지가 된다). ruff·compileall·pytest 를 실행한다.
- **환경 통일** — `pyproject.toml` 에 `[project].requires-python=">=3.12,<3.13"` 을 명시하고, `.python-version`·Dockerfile·ai-ci 를 전부 3.12 로 맞췄다. lock 파일도 도입했다. `requirements.lock`/`requirements-dev.lock`(uv 로 생성, `--universal` 마커 사용)이며 CI 와 Docker 는 lock 으로 설치한다.

## 결정

- **Python 3.12 로 통일하고 상한을 `<3.13` 으로 둔다.** GraphRAG 스택(torch/transformers/igraph)의 wheel 이 최신 Python 을 늦게 지원하므로 3.12 가 안전하다. 3.13 지원이 확산되면 상한 완화를 재검토한다.
- **pgvector 이미지는 `0.8.5-pg16` 에 digest 까지 고정한다.** 운영과 back `compose.yaml` 의 실제 이미지와 digest 까지 일치시켜 재현성을 확보했다. 롤링 태그 `pg16` 은 금지한다. 태그만 고정하는 것도 금지한다. 같은 태그가 다른 이미지를 가리킬 수 있기 때문이다. 경위를 남긴다. 최초 결정은 `0.8.1-pg16` 태그 고정이었고 근거는 "back `compose.yaml` 과 일치한다"였다. 그런데 back#31 이 compose 를 `0.8.5-pg16@sha256:1d53…` 로 올리면서 그 근거가 무효가 됐다(운영도 0.8.5 다. infra#41). 어긋난 쪽이 ai 였으므로 `S15P11A705-122` 에서 digest 까지 맞췄다.
- **lock 파일을 도입한다.** 합류자의 환경 재현성을 위해서다. `requirements.txt` 는 사람이 읽는 하한 명세로, lock 은 정확한 버전 고정으로 역할을 나눈다.
- **ai-ci 의 PR 제목 Jira 키 검증.** 형식만 보증하며 티켓이 실제로 존재하는지는 보증하지 않는다. 이 한계는 수용했다.

## 발견 (→ 트러블슈팅)

로컬 코드 리뷰로는 드러나지 않고 CI 러너 또는 멀티 커넥션 환경에서만 실패하는 경계 이슈 3건을 [troubleshooting/2026-07-24-e3-ci-and-search-path.md](../troubleshooting/2026-07-24-e3-ci-and-search-path.md)에 기록했다.

- **T21 search_path** (최우선): `SET search_path = ai` 단독 설정이 public 스키마를 검색 경로에서 제외해, VECTOR 타입 해석과 `register_vector` 가 멀티 커넥션에서 실패했다. `ai, public` 으로 보정했다. 프로덕션 `app/core/db.py` 의 유일한 변경이다.
- **T19 lock 플랫폼 종속**: Windows 에서 생성한 lock 에 포함된 `pywin32` 가 Linux CI 의 설치를 깨뜨렸다. `uv pip compile --universal` 로 플랫폼 마커를 넣어 해결했다.
- **T20 pytest pythonpath**: CI 러너에서 `app`/`tests` 모듈 import 가 실패했다. `pyproject` 에 `pythonpath=["."]` 를 추가해 해결했다.

## 검증

- `pytest -q` 27 passed(단위·저장소·API). `ruff check .` 통과. `docker build`(3.12-slim) 성공.
- CI: PR #16 이 새 워크플로로 Linux 전체 검증(45s, testcontainers pytest 포함)을 통과했고, main push CI 도 통과했다.

## E3-PR2 (완료, ai#18)

- `test_pipeline.py` — integration-tests.md §3 의 파이프라인 시나리오 20개(취소 거부·검색 경계·Keyword·재개/상태·계약위반/경합)를 구현했다. 시나리오 6·18 이 함수를 공유해 19함수다. 동시성 시나리오는 `on_call` 훅으로 CANCELLED 를 주입해 재현하며 sleep 을 쓰지 않는다.
- `pytest tests/ -q` 52 passed. 구성은 AI 검증 46개(저수준 27 + 파이프라인 19)에 CI 계약 6개를 더한 것이다. CI 계약 6개는 ai#25(인프라 티켓 `-20`)가 `tests/` 에 추가한 CI 이미지 발행 계약 테스트다. 즉 E3 자체는 46개로 완결이고, 52는 그 위에 다른 작업이 더해 늘어난 수다. ruff 통과.

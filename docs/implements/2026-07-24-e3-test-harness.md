# E3 통합 테스트 하네스 + 저수준 계층 + 인프라 정비

- **상태**: 완료 (E3-PR1). 파이프라인 계층(E3-PR2)은 미착수
- **날짜**: 2026-07-24
- **관련 PR**: [ai#14](https://github.com/Team-PinLog/ai/pull/14)(E3-PR1), [ai#16](https://github.com/Team-PinLog/ai/pull/16)(핫픽스)
- **근거 계약**: [spec/integration-tests.md](../spec/integration-tests.md) (§16 검증 시나리오)

## 무엇을 만들었나

계약 §16 검증 시나리오를 자동 pytest 스위트로 옮기기 위한 **하네스 + 저수준 3계층(단위·저장소·API)**을 구현했다. 파이프라인 시나리오 20개(`test_pipeline.py`, §3)는 E3-PR2로 분리해 미착수. 챗봇/GraphRAG 합류 대비 환경 통일(Python 3.12·lock)도 함께 반영했다. 프로덕션 `app/`은 `db.py` search_path 보정 1건만 변경(T21).

## 하네스 (`tests/`)

- **`conftest.py`**: 세션 스코프 `PostgresContainer("pgvector/pgvector:0.8.1-pg16")`(back `compose.yaml`과 태그 일치) → `schema/ai_snapshot.sql`(back V1/V100/V101 파생) 적용 → asyncpg 풀. **격리는 TRUNCATE**(동시성 테스트가 여러 커넥션을 쓰므로 롤백 격리 불가). `settings` fixture는 Profile을 주입(리터럴 금지).
- **`fakes.py`**: `FakeEmbeddingClient`/`FakeLLMClient` — sha256 기반 **결정론 벡터**(무작위는 유사도 순서 단언을 흔듦), **호출 횟수 기록**(여러 시나리오의 핵심 단언이 "호출 안 함"/"정확히 한 번"), `on_call` 훅(모델 호출과 저장 사이 창을 결정론적으로 재현, sleep 금지).
- **`builders.py`**: `make_state`/`make_embedding`/`make_preset`. `embedding_profile`·`is_deleted`·두 status를 항상 명시. **본문 버전 인자를 두지 않음**(제거된 개념 부활 방지) — 수정은 `context_id`가 다른 두 State로 표현.
- **`schema/ai_snapshot.sql`**: 테스트 전용 스냅샷. `ai` 스키마만(ai 테이블은 core FK 없음). 헤더에 back 파생 출처·갱신 위험 명시.

## 저수준 27케이스 (계층 배분)

| 계층 | 파일 | 검증 |
|---|---|---|
| 단위(DB 없음) | `test_unit.py` | 오류 분류, 후보 TOP-K(`_topk`), LLM 매핑·폐기(`_map` — 후보 밖/범위 밖 confidence 폐기·중복 접기), Profile 검증(config validator) |
| 저장소(실제 DB) | `test_repo.py` | 조건부 전이 rowcount(PENDING·만료 PROCESSING만), **is_deleted 제외 UPSERT 회귀**, delete-insert, 검색 DISTINCT ON(대표 contextId) |
| API(실제 DB, Fake 주입) | `test_api.py` | 202, 검색 형식(contextId 포함), Profile 422, 시크릿 401 |

## 인프라

- **`Dockerfile`**: `python:3.12-slim`, `requirements.lock` 설치, 비루트, `uvicorn app.main:app`.
- **`ai-ci.yml` 정비**(신규 생성 아님 — 기존 워크플로 수정): lock 설치 전환, **PR 제목 Jira 키 검증**(squash 병합이라 제목=최종 커밋), ruff·compileall·pytest.
- **환경 통일**: `pyproject.toml [project].requires-python=">=3.12,<3.13"`, `.python-version`, Dockerfile·ai-ci 전부 3.12. **lock 도입**: `requirements.lock`/`requirements-dev.lock`(uv, `--universal` 마커) — CI·Docker는 lock 설치.

## 결정

- **Python 3.12 통일, 상한 `<3.13`** — GraphRAG 스택(torch/transformers/igraph) wheel 안전판. 3.13 지원 확산 시 완화 재검토.
- **pgvector `0.8.1-pg16` 고정** — back `compose.yaml`·Testcontainers와 일치(재현성). 롤링 `pg16` 금지.
- **lock 도입** — 합류자 재현성. `requirements.txt`(사람용 하한) + lock(정확 버전).
- **ai-ci PR 제목 Jira 키 검증** — 형식 보증(티켓 존재 보증 아님, 수용).

## 발견 (→ 트러블슈팅)

로컬 코드 리뷰로는 드러나지 않고 CI 러너/멀티 커넥션에서만 터진 경계 이슈 3건을 [troubleshooting/2026-07-24-e3-ci-and-search-path.md](../troubleshooting/2026-07-24-e3-ci-and-search-path.md)에 기록:

- **T21 search_path**(최우선): `SET search_path = ai` 단독이 public을 제외해 VECTOR 타입 해석·`register_vector`가 멀티 커넥션에서 실패 → `ai, public` 보정(프로덕션 `app/core/db.py` 유일 변경).
- **T19 lock 플랫폼 종속**: Windows lock의 `pywin32`가 Linux CI 설치를 깨뜨림 → `uv pip compile --universal`.
- **T20 pytest pythonpath**: CI 러너에서 `app`/`tests` import 실패 → `pyproject pythonpath=["."]`.

## 검증

- `pytest -q` **27 passed**(단위·저장소·API), `ruff check .` clean, `docker build`(3.12-slim) 성공.
- CI: PR #16이 새 워크플로로 Linux 전체 검증(45s, testcontainers pytest 포함) 그린, main push CI 그린.

## 남은 것

- **E3-PR2**: `test_pipeline.py` — integration-tests.md §3의 파이프라인 시나리오 20개(취소 거부·검색 경계·Keyword·재개/상태·계약위반/경합). 동시성은 `on_call` 훅으로 CANCELLED 주입(sleep 금지). 미착수.

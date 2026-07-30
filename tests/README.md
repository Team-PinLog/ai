# 테스트 컨벤션

AI 서버 통합 테스트 규칙. 계약 근거는 [`docs/spec/integration-tests.md`](../docs/spec/integration-tests.md).

- **Testcontainers pgvector `0.8.5-pg16`** 사용 (SQLite·H2 금지). 검증 대상이 조건부 UPDATE
  영향 행 수·`FOR UPDATE`·`<=>`·`ON CONFLICT` SET 절이라 전부 방언 의존적. **digest까지 고정**한다
  (`conftest.py`의 `PGVECTOR_IMAGE`) — 태그만 고정하면 같은 태그가 다른 이미지를 가리킬 수 있다.
  값의 **정본은 back `compose.yaml`**이며 운영 pgvector도 0.8.5다. back이 올리면 이쪽도 따라 올린다.
- **외부 API는 인터페이스 레벨 Fake**([fakes.py](fakes.py)), HTTP mock 아님. **호출 횟수 기록
  필수** — "호출 안 함"/"정확히 한 번"이 여러 시나리오의 핵심 단언.
  이 규칙은 **파이프라인이 client를 무엇으로 대체하는가**에 대한 것이다(integration-tests.md §4.2).
  **client 자신의 HTTP 계층은 예외**이며 [test_client_retry.py](test_client_retry.py)가
  `httpx.MockTransport`로 검증한다 — 상태 코드→오류 타입 매핑은 인터페이스 Fake로 볼 수 없고,
  그 공백이 429를 영구 오류로·LLM 401을 일시 오류로 둔 채 남긴 원인이었다(`S15P11A705-121`).
- **오류 경로도 Fake로 주입한다** — `raise_exc`로 `TransientError`/`PermanentError`를 넣어
  상태가 PROCESSING으로 남는지·해당 단계만 FAILED가 되는지 단언한다. 주입 파라미터를 두고
  쓰지 않으면 그 경로는 한 번도 실행되지 않는다.
- **격리는 TRUNCATE**([conftest.py](conftest.py)). 동시성 테스트가 여러 커넥션을 쓰므로 트랜잭션
  롤백 격리 불가.
- **Profile 문자열 리터럴 금지** — `settings` fixture 경유(model-profile.md §2.1).
- **동시성은 `on_call` 훅으로 순서 고정, `sleep` 금지**. 모델 호출과 저장 사이 창을 결정론적으로 재현.
- **데이터 빌더**([builders.py](builders.py))에 본문 버전 인자를 두지 않는다. 수정은 `context_id`가
  다른 두 State로 표현(계약 §4.2).
- **외부 실호출을 CI에 넣지 않는다.** `app.smoke.gms_roundtrip`은 `_CHECKS`를 스텁으로 교체해
  집계·종료 코드·값 미노출 규약만 검증한다. 실제 GMS 왕복은 배포 절차에서 수동 실행한다 —
  실호출을 CI에 넣으면 GMS 가용성이 CI 성패에 들어온다.

## 계층 (integration-tests.md §5)

| 파일 | 계층 | DB |
|---|---|---|
| `test_unit.py` | 오류 분류(상태 코드 표·백오프 수열)·TOP-K·LLM 매핑·Profile 검증·`GMS_BASE_URL` 형식·스모크 집계 | 없음 |
| `test_repo.py` | 조건부 UPDATE rowcount·UPSERT·delete-insert·검색 Query | 실제 |
| `test_api.py` | 202·검색 형식·422·401·프로브(`/health` 불변, `/ready` 200/503) | 실제 |
| `test_pipeline.py` | §16 시나리오 전체 | 실제 |

> `test_client_retry.py`도 위 §5 계층 밖이다 — **client 호출 단위 방어**(failure-recovery.md §3.1)
> 계층이며 DB·Docker·네트워크가 필요 없다. `RetryPolicy`의 `sleep`·`jitter`를 주입해 백오프
> 수열을 값으로 단언하므로 **실제로 잠들지 않는다**. 재시도 테스트에 실제 대기를 넣지 않는다.

> `test_ci_image_publish_contract.py`는 위 §5 계층에 속하지 않는다 — `.github/workflows/ai-ci.yml`을 계약으로 고정하는 **CI 이미지 발행 계약**이며 **인프라 파트(`-20`) 소관**이다. `pytest tests/` 범위엔 포함되나 **DB·Docker가 필요 없다**(`pytest tests/test_ci_image_publish_contract.py`만 따로 돌리면 컨테이너 없이 검증). AI 파트가 `ai-ci.yml`을 바꾸면 이 테스트가 깨지므로 인프라에 요청·조율한다.

## 실행

```bash
pytest tests/ -v          # Docker 필요(Testcontainers가 pgvector 기동)
```

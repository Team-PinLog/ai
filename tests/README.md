# 테스트 컨벤션

AI 서버 통합 테스트 규칙. 계약 근거는 [`docs/spec/integration-tests.md`](../docs/spec/integration-tests.md).

- **Testcontainers pgvector `0.8.1-pg16`** 사용 (SQLite·H2 금지). 검증 대상이 조건부 UPDATE
  영향 행 수·`FOR UPDATE`·`<=>`·`ON CONFLICT` SET 절이라 전부 방언 의존적. 태그는 back
  `compose.yaml`과 일치 유지.
- **외부 API는 인터페이스 레벨 Fake**([fakes.py](fakes.py)), HTTP mock 아님. **호출 횟수 기록
  필수** — "호출 안 함"/"정확히 한 번"이 여러 시나리오의 핵심 단언.
- **격리는 TRUNCATE**([conftest.py](conftest.py)). 동시성 테스트가 여러 커넥션을 쓰므로 트랜잭션
  롤백 격리 불가.
- **Profile 문자열 리터럴 금지** — `settings` fixture 경유(model-profile.md §2.1).
- **동시성은 `on_call` 훅으로 순서 고정, `sleep` 금지**. 모델 호출과 저장 사이 창을 결정론적으로 재현.
- **데이터 빌더**([builders.py](builders.py))에 본문 버전 인자를 두지 않는다. 수정은 `context_id`가
  다른 두 State로 표현(계약 §4.2).

## 계층 (integration-tests.md §5)

| 파일 | 계층 | DB |
|---|---|---|
| `test_unit.py` | 오류 분류·TOP-K·LLM 매핑·Profile 검증 | 없음 |
| `test_repo.py` | 조건부 UPDATE rowcount·UPSERT·delete-insert·검색 Query | 실제 |
| `test_api.py` | 202·검색 형식·422·401 | 실제 |
| `test_pipeline.py` | §16 시나리오 전체 | 실제 |

## 실행

```bash
pytest tests/ -v          # Docker 필요(Testcontainers가 pgvector 기동)
```

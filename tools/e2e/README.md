# E2E 검증 드라이버

실제 GMS를 호출하는 **실경로 검증** 도구입니다. Fake 기반 `tests/`(46 케이스, Docker만 필요)와 달리
**실제 DB·실제 API 키·기동 중인 서버**가 필요합니다.

검증 결과와 판단 근거: [docs/implements/2026-07-27-e2e-verification.md](../../docs/implements/2026-07-27-e2e-verification.md)
환경 이슈(T22~T24): [docs/troubleshooting/2026-07-27-e2e-env-issues.md](../../docs/troubleshooting/2026-07-27-e2e-env-issues.md)

## 전제

`README.md` 로컬 기동 1~5단계가 끝나 있어야 합니다 — pgvector 기동, `ai.*` 스키마 적용,
`.env` 설정, `python -m app.bootstrap.load_presets`, 서버 기동.

## 실행

레포 루트에서 실행합니다(경로는 스크립트가 스스로 잡습니다).

```bash
python tools/e2e/run_pipeline.py       # PENDING 선삽입 → /context/process → COMPLETED 폴링
python tools/e2e/run_search.py         # 계약 방어선 + 검색 품질·분리도·집계·격리
python tools/e2e/run_equivalence.py    # 하네스 ↔ 운영 동등성 (후보·판정)
python tools/e2e/run_attribution.py    # 동등성 불일치의 원인 귀속 (반복 측정)
```

`--base`로 대상 서버를 바꿀 수 있습니다. Docker 컨테이너 검증에 씁니다.

```bash
python tools/e2e/run_search.py --base http://localhost:8001
```

`run_equivalence.py`·`run_attribution.py`는 `tools/keyword_eval/`의 하네스를 import하므로
그쪽 의존성(`tools/keyword_eval/requirements.txt`)과 키 설정이 함께 필요합니다.

## 구성

| 파일 | 역할 |
|---|---|
| `e2e_contexts.yaml` | 투입 Context 8건. `context_id`/`record_id` ↔ 본문·장소명 **매핑 파일** |
| `_common.py` | 레포 루트 해석 + `get_settings()` 재사용 + `--base` 파싱 |
| `run_pipeline.py` | Spring 대행(PENDING 선삽입) 후 처리 파이프라인 구동 |
| `run_search.py` | 검색 계약·품질 검증 |
| `run_equivalence.py` | 하네스와 운영의 후보·판정 비교 (비결정성 기준선 포함) |
| `run_attribution.py` | 불일치 사례를 반복 측정해 원인 귀속 |

## `e2e_contexts.yaml`이 매핑 파일인 이유

`/search`는 `recordId`·`contextId`·`similarity`만 돌려줍니다. 본문 조립은 Spring이 `core`에서
하는 구조라(`docs/spec/personal-search.md` §6), AI 단독 시연에서는 **숫자만 보입니다**.

이 파일이 id ↔ 본문·장소명을 들고 있어 `run_search.py`가 결과에 장소명을 붙입니다.
**`core` 테이블을 만들지 않고 시연 가치를 확보하는 방식**이며, 데모 시딩도 이 구조를 확장합니다.

## 주의

- **실제 GMS를 호출합니다.** 임베딩·판정 비용이 발생합니다.
- **`e2e_contexts.yaml`의 id는 검증 전용 대역**(user 9001·9002 / record 5xxx / context 1xxx)입니다.
  실제 데이터가 있는 DB에 그대로 쓰지 마세요.
- **벡터 컬럼을 읽는 스크립트는 반드시 `app.core.db.Database`를 씁니다.** raw `asyncpg`로 붙으면
  `register_vector`가 등록되지 않아 벡터가 문자열로 디코딩됩니다(T23).
- **한글 본문 요청은 `curl` 대신 Python 클라이언트로 보냅니다**(T24).

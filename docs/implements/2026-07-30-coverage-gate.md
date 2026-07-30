# app coverage 게이트 활성화 (S15P11A705-110)

상태: 완료 · 유형: 구현 · 근거: [CONTRIBUTING.md](../../CONTRIBUTING.md) 검증 절, [integration-tests.md](../spec/integration-tests.md) §4.2 · §5

`S15P11A705-108`이 비차단으로 도입한 `app` line·branch coverage 측정을 **병합 차단 게이트**로
전환했다. 티켓 완료 조건은 *"line과 branch 각각 80% 이상"*이다.

## 1. 기준선이 낡아 있었다

티켓 본문의 수치는 2026-07-28(52 tests) 관측이다. 그 사이 `S15P11A705-121`(ai#44, `b45aa93`)이
client 재시도·오류 분류 테스트 58개를 추가해 상황이 바뀌었다. **착수 시점에 재측정했다.**

| | 티켓 기재 (07-28) | 재측정 (`b45aa93` 시점) | 최종 |
|---|---|---|---|
| tests | 52 | 146 | **180** |
| line | 76.95% (474/616) | 88.80% (674/759) | **99.74% (757/759)** |
| branch | 62.50% (55/88) | 82.08% (87/106) | **98.11% (104/106)** |

티켓이 지목한 미검증 4개 영역 중 **둘은 이미 `-121`이 덮은 뒤**였다
(`client/embedding_client.py` 96%, `client/llm_client.py` 100%). 남은 것은 티켓의 추정대로
`bootstrap/load_presets.py`(**0%**)와 `main.py`(58%)였고, 재측정으로 `smoke/gms_roundtrip.py`(76%)가
추가로 드러났다.

기준선만 놓고 보면 두 지표 모두 이미 80%를 넘겨 게이트를 그대로 켤 수 있었다. 그러지 않은
이유는 **branch 82.08%가 2 포인트 여유뿐**이었기 때문이다. 그 상태로 켜면 다음 기능 PR 하나가
분기를 몇 개 추가하는 것만으로 무관한 이유로 붉어진다. 게이트는 회귀를 막아야지 정상 작업을
막으면 안 된다.

## 2. 왜 `--cov-fail-under`가 아닌가

`--cov-branch`를 켜면 coverage.py의 `percent_covered`는 **statement와 branch를 합산한 하나의
비율**이 된다. 이 레포 기준선이 그 함정을 그대로 보여 준다.

```text
합산 (pytest 터미널 표시)   88%      ← --cov-fail-under=80 통과
line                       88.80%
branch                     82.08%
```

statement 759개가 branch 106개를 압도하므로, branch가 60%대로 떨어져도 합산값은 80을 넘긴다.
완료 조건이 "각각"인 이상 합산 게이트는 조건을 검사하지 못한다. 그래서
`tools/check_coverage_gate.py`가 `coverage.json`의 `totals`를 두 지표로 나눠 판정한다.

**임계값은 스크립트 상수이며 CLI로 덮을 수 없다.** 덮을 수 있게 두면 CI가 조용히 낮은 값을
넘겨 게이트를 무력화할 수 있다. `test_ci_image_publish_contract.py`가 워크플로에
`--cov-fail-under`가 없고 게이트 step이 인자 없이 호출되는지를 계약으로 고정한다.

`--cov-branch` 없이 만든 리포트는 `coverage.json`에 branch 키 자체가 없다(실측). 그 상태를
통과로 처리하면 branch 게이트가 사라진 것과 같으므로 **판정 불가로 끊는다** — "측정하지
못했다"는 통과가 아니다.

## 3. 게이트가 실제로 막는 것을 관측했다

통과만 확인하면 아무것도 강제하지 않는 게이트를 놓친다. 네 가지 RED를 실측했다.

| 드릴 | 방법 | 결과 |
|---|---|---|
| 테스트 제거 | `pytest tests/test_unit.py`만 실행 | `line 42.82% / branch 34.91%` → **exit 1** |
| 임계값 상향 | 상수를 `99.9`로 잠깐 변경 후 전량 실행 | `line 99.74% / branch 98.11%` → **exit 1** (되돌림) |
| 측정 플래그 누락 | `--cov-branch` 없이 리포트 생성 | 판정 불가 → **exit 1** |
| 리포트 부재 | `coverage.json` 없이 실행 | 판정 불가 → **exit 1** |

임계값 상향 드릴에서는 `test_gate_thresholds_are_the_ticket_completion_criteria`도 함께 붉어졌다 —
임계값을 조용히 내리면 게이트는 남고 의미만 사라지므로 그 값을 테스트로 못박아 두었다.

## 4. 보강한 테스트 (146 → 180, +34)

전부 **계약과 실패 경로** 중심이다. 커버리지를 채우려고 만든 테스트가 아니라, 재측정으로
드러난 "한 번도 실행된 적 없는 경로"에 단언을 붙인 것이다.

### 4.1 부트스트랩 — `tests/test_bootstrap.py` (신설, 9)

`load_presets.py`는 line·branch 모두 0%였다. `/search`·`/context/process` 이전에 반드시 1회 도는
경로인데 한 줄도 검증되지 않았다.

- Preset 적재 결과: 행 수·`embedding_profile`·`is_active`·벡터 차원. **Profile과 `is_active`는
  YAML이 아니라 적재가 채우는 값**이라 어긋나면 서버가 Preset 0건으로 기동 실패한다.
- `embed()` **정확히 1회** 호출과 입력 텍스트 값 대조(§4.2 호출 횟수 기록).
- 멱등성 + `ON CONFLICT DO UPDATE` SET 절: 행을 손으로 흔든 뒤 재실행이 YAML로 되돌리는지.
- 커넥션 반납(`finally: disconnect()`) — `pg_stat_activity` 백엔드 수 대조.
- `python -m app.bootstrap.load_presets` 실행 경로.

### 4.2 기동 — `tests/test_lifespan.py` (신설, 4)

`test_api.py`는 lifespan을 **우회하고** `app.state`에 Fake를 직접 꽂는다. 그래서 실제 기동
경로가 통째로 미검증이었다.

- 조립 결과와 Preset 캐시 적재, 풀이 실제로 살아 있는지(`SELECT 1`).
- Preset 0건 기동 중단 두 경로: **Profile 불일치**와 **전부 BLOCKED**. 후자는 행은 있지만
  적재 건수가 0인 경우로, 조건이 `rows`가 아니라 `loaded`여야 하는 이유다.
- 종료 시 풀 반납.

여기서는 **진짜 클라이언트를 조립하는지**를 단언하므로 Fake로 바꾸지 않았다. 생성자는 IO를
하지 않으므로 실호출 금지 규칙과 충돌하지 않고, Fake로 바꾸면 이 단언 자체가 사라진다.

### 4.3 Keyword 단계 내부 경로 — `tests/test_pipeline.py` (+4)

- **후보 0개 → LLM 미호출**로 정상 COMPLETED. 시나리오 14(LLM이 빈 `selected` 반환)와 다른
  경로다 — 저쪽은 호출한 뒤 0개고 이쪽은 아예 부르지 않는다. 유사도 하한이 사라지면 이
  테스트만 깨진다.
- `embedding_status`는 COMPLETED인데 벡터 행이 없는 경우 → 해당 단계만 FAILED.
- 다른 워커가 embedding을 끝내 벡터를 들고 있지 않은 경합 경로의 fallback 조회.
- **사전 검사와 재개 판정 사이에 State가 삭제되는 창**. 두 조회가 서로 다른 `acquire()`라
  그 사이가 열려 있다. `sleep` 대신 커넥션 반납 시점을 훅으로 잡아 순서를 고정했다(§4.4의
  `on_call` 훅과 같은 기법, 창의 위치만 다르다).

### 4.4 단위 (+16), CI 계약 (+1)

캐시 스냅샷 노출 필드, 적재 전 조회 오류, pgvector↔파이썬 변환 양쪽 분기, 중복 confidence
접기의 내림차순·`None` 짝 케이스, `_silence_http_logging`, 스모크 스크립트 실행(성공/실패
양쪽, 실패 시 값 미노출), 게이트 판정 로직의 임계값 경계.

### 4.5 테스트 인프라

`conftest.py`의 `_schema`를 async → **sync fixture**로 바꿨다. 스크립트 엔트리포인트는 자기가
`asyncio.run()`을 부르므로 그것을 검증하는 테스트도 sync여야 하고, sync 테스트는 async fixture를
받을 수 없다. 스키마 적용은 세션당 1회라 비용은 같다. 동기 테스트용 `clean_dsn`도 함께 추가했다.

`if __name__ == "__main__"` 아래는 import로 한 줄도 실행되지 않으므로 `runpy.run_module(...,
run_name="__main__")`로 검증한다. runpy는 **새 네임스페이스**에서 모듈을 다시 실행하므로 캐시된
모듈에 건 패치가 보이지 않는다 — 원본 모듈(`app.client.embedding_client`)의 속성을 갈아 끼워야
새 네임스페이스의 `from ... import`가 그것을 집는다. 이 함정을 `tests/README.md`에 적었다.

## 5. 제외하지 않고 미달로 남긴 분기

**`# pragma: no cover`도 `omit`도 쓰지 않았다.** 남은 미달은 2 line · 2 branch다.

| 위치 | 내용 | 판단 |
|---|---|---|
| `service/embedding_service.py:80` | `complete()` rowcount 0 → `PersistDiscarded` | 도달 불가 |
| `service/keyword_service.py:184` | 〃 (keyword 단계) | 도달 불가 |

둘 다 `db.transaction()` 안에서 `lock_state()`가 `SELECT ... FOR UPDATE`로 상태를 잠그고
`PROCESSING`임을 확인한 **직후**의 재검사다. 같은 트랜잭션에서 행 잠금을 쥔 채 조건이 뒤집힐
수 없으므로 현재 구조에서는 실행될 수 없다.

**제외하지 않은 이유**는 도달 불가가 구조에 딸린 성질이기 때문이다. 저장 로직이 잠금 밖으로
나가거나 `lock_state`와 `complete`의 조건이 갈라지면 이 줄은 도달 가능해진다. `pragma`를
붙여 두면 그때 아무도 모른다. 미달로 남겨 두면 커버리지 리포트가 계속 그 두 줄을 가리킨다.
게이트 여유가 18 포인트라 이 선택에 비용이 없다.

## 6. 함께 처리한 것 — `integration-tests.md` §4.2 계층 구분 명문화

`-121`의 후속이다. §4.2가 *"HTTP 레벨 목이 아니라 인터페이스 레벨 Fake를 씁니다"*라고만 적어,
`test_client_retry.py`의 HTTP mock이 명세 위반인지가 `-121` 작업 중 **두 번** 질문으로 올라왔다.
판정은 충돌이 아니라 **층이 다름**이었고, 명세에 없었다는 것이 같은 질문이 반복된 이유다.

§4.2 앞에 적용 범위 절을 넣고 경계를 한 문장으로 고정했다 — **`app/client/` 밖의 코드는 HTTP를
몰라야 하고, 따라서 그 코드를 검증하는 테스트도 HTTP를 몰라야 한다.** HTTP 목이 파이프라인
테스트에 나타나면 계층 위반의 신호이고, client 단위 테스트에 인터페이스 Fake가 나타나면
아무것도 검증하지 않는 테스트다. §5 계층 표에도 client 단위·부트스트랩·기동 세 행을 더했다.

`docs/spec/` 수정은 이번 작업에서 허용됐다. `-121`에서 금지한 것은 그 작업이 *구현을 계약에
맞추는 것*이었기 때문이고, 이번은 **계약 자체의 공백을 메우는 것**이다.

## 7. 검증

```bash
ruff check .                                    # exit 0
python -m compileall app tools                  # exit 0
pytest --cov=app --cov-branch --cov-report=term-missing --cov-report=json:coverage.json
python tools/check_coverage_gate.py             # exit 0
```

`180 passed`, Testcontainers pgvector `0.8.5-pg16@sha256:1d53…` 전량. Python 3.12.10 / win32.

## 8. 범위 밖

- **coverage 임계값 상향.** 현재 실측이 line 99.74% / branch 98.11%라 80%는 헐겁다. 다만
  임계값은 "지금 어디에 있나"가 아니라 "어디 아래로 내려가면 막을 것인가"이고, 후자는 티켓이
  80으로 정했다. 상향은 별건 판단이다.
- **`tools/`의 커버리지.** 게이트 대상은 `--cov=app`이므로 게이트 스크립트 자신은 측정되지
  않는다. 대신 판정 로직을 `evaluate()`로 분리해 임계값 경계를 단위 테스트로 고정했다.
- **branch protection 설정.** 중앙 소관이다. `ai-ci / check` 안의 step으로 들어가므로 required
  status check 목록은 바뀌지 않는다.

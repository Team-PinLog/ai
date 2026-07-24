# E3 CI·런타임 이슈 (T19~T21)

- **날짜**: 2026-07-24
- **상태**: 해결됨
- **맥락**: E3 통합 테스트 자동화(ai#14) + 병합 후 첫 CI 실행에서 드러난 핫픽스(ai#16)
- **관련**: [implements/2026-07-24-e3-test-harness.md](../implements/2026-07-24-e3-test-harness.md), PR [ai#14](https://github.com/Team-PinLog/ai/pull/14)·[ai#16](https://github.com/Team-PinLog/ai/pull/16)

## T19 — Windows에서 만든 lock의 플랫폼 종속 패키지가 Linux CI 설치를 깨뜨림

**증상**: E3-PR1 병합 후 main push로 처음 실행된 `ai-ci`가 `Install dependencies`에서 19초 만에 실패.
```
ERROR: Could not find a version that satisfies the requirement pywin32==312 (from versions: none)
ERROR: No matching distribution found for pywin32==312
```

**원인**: `requirements-dev.lock`을 Windows에서 `uv pip compile`로 생성하면서 **플랫폼 마커 없이** Windows 전용 패키지를 고정했다. `testcontainers → docker SDK`가 Windows에서 `pywin32`를 의존하고, `colorama`도 마찬가지다. ubuntu 러너의 `pip install -r`가 마커 없는 `pywin32`를 강제 설치하려다 실패한다.

**해결**: `--universal`로 재생성해 `sys_platform` 마커를 포함시킨다.
```bash
uv pip compile --universal requirements.txt -o requirements.lock
uv pip compile --universal requirements-dev.txt -o requirements-dev.lock
```
결과:
```
pywin32==312 ; sys_platform == 'win32'
colorama==0.4.6 ; sys_platform == 'win32'
```
Linux CI·Docker는 마커를 평가해 이들을 스킵한다.

## T20 — CI 러너에서 pytest가 app 모듈을 못 찾음

**증상**: T19 수정 후 다음 실행의 `pytest`에서 17초 실패.
```
ImportError while loading conftest '/home/runner/work/ai/ai/tests/conftest.py'.
E   ModuleNotFoundError: No module named 'app'
```

**원인**: pytest는 `conftest.py`가 있는 디렉토리(`tests/`)를 sys.path에 넣지만 레포 루트(`app`의 부모)는 넣지 않는다. 로컬에서는 `PYTHONPATH=<루트>`를 수동 설정해 검증했던 것이 이 조건 차이를 가렸다.

**해결**: `pyproject.toml`에 pytest의 `pythonpath`를 설정해 루트를 sys.path에 넣는다.
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
```
검증은 `PYTHONPATH` 없이 재현: `pytest -q` → 27 passed.

## T21 — search_path=ai 단독이 public을 제외해 VECTOR 타입·register_vector가 실패 ※ E3 최우선 발견

**증상**: 통합 테스트에서 SearchService가 쓰는 pool 커넥션에서만 검색이 실패.
```
asyncpg.exceptions.UndefinedFunctionError: operator does not exist: public.vector <=> unknown
# ::vector 캐스트를 넣자
asyncpg.exceptions.UndefinedObjectError: type "vector" does not exist
```

**원인**: `app/core/db.py`가 커넥션 초기화에서 `SET search_path = ai`만 설정했다. PostgreSQL에서 `search_path`를 명시하면 **public이 암묵 포함되지 않는다**. pgvector 확장은 `public` 스키마에 설치되므로(`public.vector`), `ai` 단독 경로에서는 `vector` 타입 이름 해석과 `register_vector`(타입 OID 조회)가 **일부 커넥션에서 실패**한다. 이전 로컬 검증(단일 요청)이 통과했던 것은 우연히 첫 커넥션만 register된 상태였기 때문이고, 멀티 커넥션을 쓰는 통합 테스트가 이 결함을 드러냈다.

**해결**: `search_path`에 `public`을 포함한다. `ai` 우선 + `public`(확장 소재).
```python
# app/core/db.py
await conn.execute("SET search_path = ai, public")
await register_vector(conn)
```
`core`는 여전히 경로에서 제외되므로, 실수로 `core.*`를 참조하는 것을 막는 원래 방어 의도는 유지된다. 이 보정은 프로덕션 코드(`app/core/db.py`)의 견고성 개선이며, 로컬 검증에서 우연히 가려졌던 결함을 테스트가 잡아낸 사례다.

## 공통 교훈

- **워크플로를 바꾸는 PR은 병합 후 첫 실행이 곧 첫 검증이다.** PR CI는 base(main)의 기존 워크플로로 돌기 때문에, 새 워크플로(lock 설치·Jira 검증·pytest 정비)는 병합된 뒤에야 처음 실행된다. 이런 PR은 병합 직후 main CI 확인을 필수 단계로 둔다.
- **로컬에서 환경변수를 수동 설정해 검증하면 CI와의 조건 차이가 가려진다.** T20은 `PYTHONPATH` 우회가, T21은 단일 커넥션이 결함을 숨겼다. 로컬 검증은 CI와 같은 조건(마커 없는 env, 멀티 커넥션)으로 재현한다.

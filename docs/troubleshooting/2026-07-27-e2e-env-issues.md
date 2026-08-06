# E2E 검증 환경 이슈 (T22~T24)

- **날짜**: 2026-07-27
- **상태**: 해결됨
- **맥락**: E2E 실경로 검증(S15P11A705-58) 중 로컬 환경·스크립트 작성에서 겪은 문제
- **관련**: [implements/2026-07-27-e2e-verification.md](../implements/2026-07-27-e2e-verification.md), [tools/e2e/](../../tools/e2e/)

세 건 모두 **시딩 작업에서 다시 만난다.** 시딩 스크립트를 새로 쓰는 사람이 같은 곳에서 멈추지 않도록 증상·원인·해결로 남긴다.

## T22 — `.env`가 CRLF라 셸로 값을 뽑으면 `\r`이 섞여 JSON이 깨짐

**증상**: `.env`에서 시크릿·Profile 값을 뽑아 `curl`로 API 를 호출하니 본문 파싱이 실패했다.

```bash
SECRET=$(grep '^INTERNAL_SHARED_SECRET=' .env | cut -d= -f2-)
curl -X POST .../internal/v1/search -H "X-Internal-Secret: $SECRET" -d "{...\"embeddingProfile\":\"$PROFILE\"}"
# {"detail":"There was an error parsing the body"}
```

**원인**: `.env`가 **CRLF 줄바꿈**으로 저장되어 있다. `cut`은 줄 끝의 `\r`까지 값에 포함시키므로, 헤더 값과 JSON 문자열 안에 제어문자가 들어간다. JSON 파서는 문자열 리터럴 내부의 raw `\r`을 거부한다.

증상이 헷갈리는 이유는 **일부 요청이 통과하기 때문**이다. `\r`이 마지막 필드에 들어가면 본문이 깨지지만, ASCII 만 쓰는 짧은 본문이나 값이 헤더에만 쓰이는 요청은 그대로 성공한다. 실제로 Profile 불일치 422 확인은 통과했고 정상 검색만 실패했다.

**해결**: 셸로 뽑을 때 `\r`을 제거한다.

```bash
SECRET=$(grep '^INTERNAL_SHARED_SECRET=' .env | cut -d= -f2- | tr -d '\r\n')
```

더 나은 해결은 **셸로 `.env`를 파싱하지 않는 것**이다. 파이썬 스크립트에서 `app.core.config.get_settings()`를 쓰면 pydantic-settings 가 CRLF 를 정상 처리하며, 값이 셸 밖으로 노출될 위험도 없다. `tools/e2e/`의 드라이버는 전부 이 방식이다.

**관련**: [T16](2026-07-23-fastapi-local-verification.md)(`.env` UTF-8 BOM 이 첫 키 파싱을 깨뜨림)과 같은 계열의 문제다. **`.env` 파일의 바이트 표현이 파서마다 다르게 해석된다.**

## T23 — 앱 `Database`를 쓰지 않으면 `register_vector` 미등록으로 벡터가 문자열로 디코딩됨 (시딩 재발 주의)

**증상**: 프리셋을 직접 읽는 스크립트가 캐시 적재에서 예외로 중단됐다.

```
File "app/cache/preset_cache.py", line 22, in _to_array
    return np.asarray(value, dtype=np.float32)
ValueError: could not convert string to float: '[0.05609131,0.008399963,-0.039398193,...]'
```

**원인**: 스크립트가 `asyncpg.connect()`를 직접 호출했다. pgvector 의 VECTOR 컬럼은 **커넥션마다 `register_vector()`로 타입을 등록해야** `Vector` 객체로 디코딩된다. 등록하지 않으면 asyncpg 가 **텍스트 표현 그대로**(`'[0.05, ...]'` 문자열) 돌려준다. `_to_array`는 `Vector`(`to_numpy()` 경유) 또는 리스트를 기대하므로 문자열에서 실패한다.

**제품 결함으로 오인하기 쉽다.** 실패하는 코드(`PresetCache.load`)는 프로덕션 코드이고, 스택 트레이스에 스크립트가 등장하지 않기 때문이다.

**진단 방법**: **같은 코드가 두 경로에서 다르게 동작하는지 본다.** `app/main.py`의 lifespan 은 같은 `PresetCache.load`로 27건을 정상 적재했다(기동 로그 `preset cache loaded: 27 presets`). 그렇다면 차이는 코드가 아니라 **연결 방식**이다. `app/core/db.py`는 커넥션 초기화에서 두 가지를 한다:

```python
await conn.execute("SET search_path = ai, public")
await register_vector(conn)
```

직접 `asyncpg.connect`를 쓰면 이 초기화를 통째로 건너뛴다.

**해결**: 스크립트도 앱의 `Database`를 그대로 쓴다.

```python
from app.core.db import Database

db = Database(settings.database_url)
await db.connect()
async with db.acquire() as conn:
    rows = await keyword_preset_repo.load_active(conn, settings.embedding_profile)
await db.disconnect()
```

우회하지 않는 편이 **검증으로서도 더 충실하다.** 앱과 동일한 연결 설정(search_path·타입 등록)을 쓰게 되므로 검증 대상과 실행 환경이 일치한다.

**예외**: 벡터 컬럼을 읽지 않는 스크립트(상태 조회·PENDING 행 삽입 등)는 raw `asyncpg`로도 동작한다. `tools/e2e/run_pipeline.py`가 그 경우다. 다만 **벡터를 한 번이라도 읽는 순간 같은 문제가 재발**하므로, 시딩 스크립트는 처음부터 `Database`를 쓰는 편이 안전하다.

**관련**: [T17](2026-07-23-fastapi-local-verification.md)(VECTOR 컬럼이 `Vector` 객체로 반환됨 — 디코드 방향), [T21](2026-07-24-e3-ci-and-search-path.md)(`search_path`에 `public` 누락 시 타입 해석 실패). 셋 다 **pgvector 타입 해석은 커넥션 단위 상태**라는 같은 원리에서 나온 문제다.

## T24 — Git Bash + `curl`에서 한글 본문이 깨짐 (ASCII는 통과)

**증상**: 컨테이너 API 를 `curl`로 확인하는데 한글 질의만 실패했다.

```bash
curl -X POST .../search -d "{\"query\":\"비 오는 날 아늑한 곳\",...}"
# {"detail":"There was an error parsing the body"}

curl -X POST .../search -d '{"query":"x","embeddingProfile":"wrong-v1"}'
# HTTP 422   ← ASCII 본문은 정상 통과
```

**원인**: Windows Git Bash 에서 명령줄 인자에 담긴 한글이 UTF-8 로 전달되지 않는다(콘솔 코드페이지·MSYS 인자 변환). 서버는 UTF-8 JSON 을 기대하므로 파싱에 실패한다. **서버 문제가 아니다.** 같은 요청을 Python `httpx`로 보내면 정상 동작한다.

T22와 **증상이 완전히 동일**해서 혼동하기 쉽다. 구분법:

```
ASCII 본문도 실패    → T22 (값에 \r 혼입)
ASCII 본문은 통과    → T24 (한글 인코딩)
```

**해결**: 한글이 들어가는 요청은 `curl` 대신 Python 클라이언트로 보낸다.

```python
import httpx
r = httpx.post(url, headers=H, json={"query": "비 오는 날 아늑한 곳", ...})
```

`httpx`의 `json=`은 UTF-8 로 직렬화하고 `Content-Type`도 맞춰 준다. `tools/e2e/`의 드라이버가 전부 이 방식이며, `curl`은 `/health`나 HTTP 코드 확인 같은 ASCII 경로에만 쓴다.

파일 경유(`curl -d @body.json`, UTF-8 로 저장)도 가능하지만, 검증 스크립트를 어차피 Python 으로 쓰게 되므로 실익이 없다.

## 공통 교훈

- **`.env`는 셸로 파싱하지 않는다.** BOM(T16)·CRLF(T22) 모두 셸 텍스트 처리에서만 문제가 됐다. `get_settings()`를 쓰면 세 문제가 한꺼번에 사라지고 값이 로그에 노출될 위험도 줄어든다.
- **검증 스크립트는 앱의 인프라 객체를 재사용한다.** T23 은 `Database`를 우회해서 생긴 문제였다. 우회하면 검증 대상과 실행 환경이 달라져, 검증이 통과해도 무엇을 통과시킨 것인지 불분명해진다.
- **같은 증상이 두 원인에서 나온다.** T22와 T24는 응답 메시지가 동일하다. 재현 조건을 좁히는 최소 실험(ASCII 본문으로 한 번 더 보내기)이 원인 분기를 가른다.

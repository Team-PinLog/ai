"""검색 경로 전체 — 실패가 **어떤 HTTP 상태로 나가는가**.

`test_client_retry.py`가 `502 → TransientError`를 정확히 검증하고 있었는데도 운영에서
검색이 500으로 실패했다(`ai#69`). 분류도 재시도도 맞았고, **그 예외가 응답이 되는 지점을
아무도 보지 않았다.** 두 테스트 사이에 계층 하나가 통째로 비어 있었다.

이 파일이 그 계층이다. `httpx.MockTransport` → 실제 `EmbeddingClient` → `SearchService`
→ router → 예외 핸들러 → HTTP 응답까지 **한 번의 요청으로 관통**한다.

- **Fake client를 쓰지 않는다.** `FakeEmbeddingClient(raise_exc=...)`로 예외를 주입하면
  핸들러의 매핑은 볼 수 있지만 분류 경로를 건너뛴다. 그러면 `classify_http_status`가
  바뀌어도 이 파일은 통과한다 — `ai#69`를 놓친 구멍이 정확히 그 모양이었다.
  `tests/README.md`의 "인터페이스 레벨 Fake" 규칙은 *파이프라인이 client를 무엇으로
  대체하는가*의 규칙이고(`integration-tests.md` §4.2), 여기서 고정하는 것은 client가
  아니라 **업스트림 상태 코드와 응답 상태 코드 사이의 계약**이다.
- **`test_api.py`와 별도 파일인 이유**는 조립이 다르기 때문이다. 저쪽은 Fake를 꽂아
  형식·인증·프로브를 보고, 여기는 전송 계층부터 실물을 세운다.
- 실제로 잠들지 않는다. `RetryPolicy.sleep`에 즉시 반환 코루틴을 주입한다.

`S15P11A705-221`에서 **DB 축**을 더했다(맨 아래 절). 업스트림과 같은 질문을 DB에 대해
묻는다 — 검색 도중 DB가 죽으면 무엇이 나가는가. 같은 원칙을 지킨다: **DB도 Fake로
바꾸지 않는다.** 예외를 주입하는 대신 실제 `asyncpg` 풀을 실제로 실패시킨다(닿지 않는
주소, 서버가 실제로 취소한 질의). 예외 객체를 손으로 만들어 넣으면 분류 경로를 건너뛰고,
그러면 `db_errors.py`의 경계가 바뀌어도 이 파일이 통과한다 — `ai#69`를 놓친 구멍과
같은 모양이다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
import httpx
import pytest

from app.client.embedding_client import EmbeddingClient
from app.client.retry import RetryPolicy
from app.core.db import Database
from app.main import create_app
from app.repository import context_embedding_repo
from app.service.search_service import SearchService

HDR = {"X-Internal-Secret": "test-secret"}

_DIM = 1536


async def _no_sleep(_delay: float) -> None:
    """백오프를 값으로 소비만 한다. 재시도 소진을 실제 대기 없이 재현한다."""


def _transport(handler):
    """handler(request, call_no) → Response. 업스트림 호출 횟수를 세기 위해 기록한다."""
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    return httpx.MockTransport(wrapped), seen


def _always(code: int, body: str = "provider says no"):
    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(code, text=body)

    return handler


def _ok(_request: httpx.Request, _n: int) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * _DIM}]})


@asynccontextmanager
async def _search_api(db, settings, handler, *, raise_app_exceptions: bool = True):
    """전송 계층만 목이고 나머지는 전부 실물인 앱. `/internal/v1/search` 전용."""
    transport, seen = _transport(handler)
    embedding_client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        # jitter=항등 + sleep=즉시 반환. 백오프 수열 자체는 test_client_retry.py가 본다.
        retry=RetryPolicy(sleep=_no_sleep, jitter=lambda d: d),
        transport=transport,
    )
    app = create_app()
    app.state.settings = settings
    app.state.db = db
    app.state.search_service = SearchService(db, embedding_client, settings)
    # raise_app_exceptions=False는 uvicorn의 동작(잡히지 않은 예외 → 500)을 모사한다.
    # 기본값 True면 예외가 테스트로 그대로 튀어 "500이 나갔다"를 단언할 수 없다.
    asgi = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with httpx.AsyncClient(transport=asgi, base_url="http://test") as client:
        yield client, seen


@pytest.fixture
def search_api(db, settings):
    def factory(handler, *, database=None, **kw):
        # database=... 는 DB 축 전용이다. 기본값은 Testcontainers 풀(정상 DB).
        return _search_api(database or db, settings, handler, **kw)

    return factory


async def _post_search(client, settings, query: str = "카페"):
    return await client.post(
        "/internal/v1/search",
        headers=HDR,
        json={
            "userId": 1,
            "query": query,
            "limit": 10,
            "embeddingProfile": settings.embedding_profile,
        },
    )


# ── 일시 오류 → 503 (failure-recovery.md §2.1) ──────────
async def test_upstream_502_exhausts_retry_and_returns_503(search_api, settings):
    """`ai#69`의 재현. 이 단언이 없던 동안 같은 경로가 500을 냈다."""
    async with search_api(_always(502)) as (client, seen):
        r = await _post_search(client, settings)
    assert r.status_code == 503
    assert len(seen) == 3  # 재시도가 소진된 뒤에야 응답이 된다(§3.1)


@pytest.mark.parametrize("code", [429, 500, 502, 503])
async def test_upstream_transient_codes_all_return_503(search_api, settings, code):
    """분류표(§2.1)의 일시 오류 집합이 하나의 응답 상태로 모인다."""
    async with search_api(_always(code)) as (client, _):
        r = await _post_search(client, settings)
    assert r.status_code == 503


async def test_upstream_timeout_returns_503(search_api, settings):
    def handler(request: httpx.Request, _n: int) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    async with search_api(handler) as (client, seen):
        r = await _post_search(client, settings)
    assert r.status_code == 503 and len(seen) == 3


# ── 영구 오류 → 502 (failure-recovery.md §2.2) ──────────
@pytest.mark.parametrize("code", [400, 401, 403, 404])
async def test_upstream_permanent_codes_return_502(search_api, settings, code):
    """키·모델명·base URL 설정 문제. 재시도해도 같은 답이므로 503과 구분한다."""
    async with search_api(_always(code)) as (client, seen):
        r = await _post_search(client, settings)
    assert r.status_code == 502
    assert len(seen) == 1  # 영구 오류는 재시도하지 않는다


async def test_dimension_mismatch_returns_502(search_api, settings):
    """200을 받고도 쓸 수 없는 응답. Profile이 어긋난 배포에서 이 칸만 올라간다."""

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1] * (_DIM - 1)}]}
        )

    async with search_api(handler) as (client, _):
        r = await _post_search(client, settings)
    assert r.status_code == 502


# ── 500은 「우리 코드의 결함」으로 남는다 ────────────────
async def test_unclassified_exception_still_returns_500(search_api, settings):
    """분류되지 않은 예외까지 5xx로 뭉뚱그리지 않는다.

    이 단언이 이 티켓의 절반이다. 502·503으로 갈라내는 값어치는 **남은 500이 무엇을
    뜻하는지 확정된다**는 데 있다 — 500이 로그에 보이면 업스트림이 아니라 우리다.
    """

    def handler(_request: httpx.Request, _n: int) -> httpx.Response:
        raise RuntimeError("neither transient nor permanent")

    async with search_api(handler, raise_app_exceptions=False) as (client, _):
        r = await _post_search(client, settings)
    assert r.status_code == 500


# ── 회귀: 살아 있는 경로를 죽이지 않았는가 ───────────────
async def test_upstream_recovery_within_retry_returns_200(search_api, settings):
    """502 두 번 뒤 성공. 전 경로(임베딩→DB→응답)가 그대로 산다."""

    def handler(request: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(502, text="x") if n < 3 else _ok(request, n)

    async with search_api(handler) as (client, seen):
        r = await _post_search(client, settings)
    assert r.status_code == 200 and r.json() == {"results": []}
    assert len(seen) == 3


async def test_profile_mismatch_still_422(search_api, settings):
    """422 핸들러가 새 핸들러 둘에 가려지지 않는다. 임베딩을 호출하지도 않는다."""
    async with search_api(_ok) as (client, seen):
        r = await client.post(
            "/internal/v1/search",
            headers=HDR,
            json={"userId": 1, "query": "x", "limit": 5,
                  "embeddingProfile": "wrong-profile-v9"},
        )
    assert r.status_code == 422 and r.json()["serverProfile"] == settings.embedding_profile
    assert seen == []


async def test_missing_secret_is_401_before_any_upstream_call(search_api, settings):
    """인증 실패가 업스트림보다 먼저다 — 새 핸들러가 미들웨어보다 안쪽에 있다."""
    async with search_api(_always(502)) as (client, seen):
        r = await client.post(
            "/internal/v1/search",
            json={"userId": 1, "query": "x", "limit": 5,
                  "embeddingProfile": settings.embedding_profile},
        )
    assert r.status_code == 401 and seen == []


# ── 응답이 무엇을 말하지 않는가 (probe.py 기준의 검색 경로판) ──
@pytest.mark.parametrize("code", [502, 401])
async def test_error_response_exposes_no_configured_values(search_api, settings, code):
    """credential·endpoint·profile 값을 어떤 분기에서도 싣지 않는다.

    `app/api/probe.py`가 무인증 경로에 세운 기준을 여기에도 적용한다. 이 경로는 공유
    시크릿 뒤이지만 응답은 `back` 로그를 거쳐 흘러가고, 업스트림 본문에는 게이트웨이가
    무엇을 되돌렸는지가 그대로 들어 있다.
    """
    async with search_api(_always(code, body="key sk-live-1234 rejected at gms.example")) as (
        client,
        _,
    ):
        r = await _post_search(client, settings)
    body = r.text
    for value in (
        settings.gms_base_url,
        settings.gms_api_key,
        settings.internal_shared_secret,
        settings.database_url,
        settings.embedding_profile,
        "sk-live-1234",
        "gms.example",
    ):
        assert value not in body


async def test_error_response_does_not_echo_the_user_query(search_api, settings):
    """검색어는 사용자가 쓴 문장이다. 오류 응답에 되비추지 않는다."""
    async with search_api(_always(502)) as (client, _):
        r = await _post_search(client, settings, query="비 오는 날 혼자 갔던 그 카페")
    assert "카페" not in r.text


# ══ DB 축 (S15P11A705-221, failure-recovery.md §2.5) ══════════════════════
#
# 임베딩은 성공하고 **그 다음 DB가 실패한다**. 그래서 아래 전부 `_ok` 트랜스포트를 쓴다 —
# 업스트림이 멀쩡한데도 503이 나가는 것이 이 절의 요점이다.

# 접속 불가를 재현할 DSN. 실제 자격 증명이 아니라 이 파일이 지어낸 값이며, 아래
# 노출 테스트가 **이 값들이 응답에 없음**을 단언하는 데 그대로 쓴다.
_DEAD_HOST = "127.0.0.1"
_DEAD_PORT = 1
_DEAD_USER = "probe-user"
_DEAD_PASSWORD = "probe-password-not-a-real-secret"
_DEAD_DSN = (
    f"postgresql://{_DEAD_USER}:{_DEAD_PASSWORD}@{_DEAD_HOST}:{_DEAD_PORT}/pinlog"
)


class _LazyPoolDatabase(Database):
    """`min_size=0` 풀. 접속을 첫 `acquire()`까지 미룬다.

    운영 `Database.connect()`는 `min_size=1`이라 **기동 시점**에 접속이 깨진다 — 그건
    lifespan 실패이지 요청 경로가 아니다. 이 티켓이 겨냥한 것은 *서버가 떠 있는 동안*
    DB가 닿지 않게 되는 쪽(풀 재충전 실패·DB 재기동)이고, `min_size=0`이 그 순간을
    요청 안으로 옮겨 놓는다. 풀·드라이버·예외는 전부 실물이다.
    """

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=0, max_size=1)


def _repo_runs(monkeypatch, sql: str):
    """저장소의 검색 질의만 다른 SQL로 바꾼다.

    **커넥션도 세션 경계도 실물이다.** 예외를 만들어 던지는 대신 서버가 실제로 그 오류를
    내게 한다 — 분류 경로(`db.acquire()` → `db_errors.py`)를 건너뛰지 않기 위해서다.
    """

    async def fake_search(conn, *_args, **_kwargs):
        return await conn.fetch(sql)

    monkeypatch.setattr(context_embedding_repo, "search", fake_search)


# ── 일시 오류 → 503 ─────────────────────────────────────
async def test_db_unreachable_returns_503(search_api, settings):
    """DB에 닿지 않는다 → 503. 이 티켓의 본체.

    이전에는 500이었다 — 커넥션 풀 고갈이나 DB 재기동처럼 **기다리면 낫는 상황**이
    "우리 코드가 깨졌다"와 같은 코드를 썼고, `-220`이 500을 비워 둔 전제가 그만큼
    깨져 있었다.

    접속 실패는 `asyncpg` 예외가 아니라 stdlib `OSError`(`ConnectionRefusedError`)로
    온다. 그것이 이 경로에서 가장 놓치기 쉬운 사실이라 여기서 못박는다.
    """
    dead = _LazyPoolDatabase(_DEAD_DSN)
    await dead.connect()
    try:
        async with search_api(_ok, database=dead) as (client, seen):
            r = await _post_search(client, settings)
    finally:
        await dead.disconnect()
    assert r.status_code == 503
    assert len(seen) == 1  # 임베딩은 성공했다. 실패한 것은 그 다음이다


async def test_db_connection_dropped_midflight_returns_503(
    search_api, settings, monkeypatch
):
    """질의 도중 커넥션이 끊긴다 → 503. DB 재기동이 이 모양으로 보인다.

    서버가 실제로 백엔드를 죽이므로 `asyncpg`가 `08003`을 올린다(실측).
    """
    _repo_runs(monkeypatch, "SELECT pg_terminate_backend(pg_backend_pid())")
    async with search_api(_ok) as (client, _):
        r = await _post_search(client, settings)
    assert r.status_code == 503


async def test_db_statement_canceled_returns_503(search_api, settings, monkeypatch):
    """서버가 질의를 취소했다(`57014`) → 503. 느린 DB는 우리 결함이 아니다."""

    async def fake_search(conn, *_args, **_kwargs):
        await conn.execute("SET statement_timeout = 50")
        return await conn.fetch("SELECT pg_sleep(1)")

    monkeypatch.setattr(context_embedding_repo, "search", fake_search)
    async with search_api(_ok) as (client, _):
        r = await _post_search(client, settings)
    assert r.status_code == 503


# ── 500은 여전히 「우리 코드의 결함」이다 ────────────────
async def test_unclassified_db_error_still_returns_500(
    search_api, settings, monkeypatch
):
    """없는 컬럼을 참조하면 500이다. **이 단언이 이 티켓의 절반이다.**

    `test_unclassified_exception_still_returns_500`과 같은 경계다. DB 실패를 통째로
    503으로 감싸면 이런 질의 결함이 "일시적으로 사용할 수 없습니다" 뒤에 영구히 숨는다 —
    재시도해도 낫지 않는데 알림은 울리지 않는다. 그래서 `42xxx`(문법·없는 컬럼/테이블·
    타입 불일치)는 분류하지 않고 500에 남긴다.
    """
    _repo_runs(monkeypatch, "SELECT no_such_column FROM ai.context_embedding")
    async with search_api(_ok, raise_app_exceptions=False) as (client, _):
        r = await _post_search(client, settings)
    assert r.status_code == 500


async def test_db_interface_misuse_still_returns_500(search_api, settings, monkeypatch):
    """`asyncpg.InterfaceError`도 500이다.

    이 한 타입이 "connection is closed"(수명주기)와 "the server expects N arguments"
    (우리 결함)를 함께 쓴다(실측). 통째로 일시 오류로 두면 후자가 숨으므로 분류하지
    않는다 — `db_errors.py`의 판단 중 가장 논쟁적인 곳이라 테스트로 고정한다.
    """

    async def fake_search(conn, *_args, **_kwargs):
        return await conn.fetch("SELECT $1::int, $2::int", 1)  # 인자 하나 모자란다

    monkeypatch.setattr(context_embedding_repo, "search", fake_search)
    async with search_api(_ok, raise_app_exceptions=False) as (client, _):
        r = await _post_search(client, settings)
    assert r.status_code == 500


# ── 응답이 무엇을 말하지 않는가 (DB판) ───────────────────
async def test_db_error_response_exposes_no_connection_details(search_api, settings):
    """접속 정보를 응답에 싣지 않는다.

    업스트림 축의 `test_error_response_exposes_no_configured_values`와 같은 기준이다.
    DSN에는 **DB 비밀번호**가 들어 있고 접속 실패 예외에는 host·port가 섞여 들어올 수
    있다. `db_errors.py`가 예외 메시지에 타입 이름과 SQLSTATE만 담는 것이 그 대응이며,
    이 단언이 그것을 지킨다.
    """
    dead = _LazyPoolDatabase(_DEAD_DSN)
    await dead.connect()
    try:
        async with search_api(_ok, database=dead) as (client, _):
            r = await _post_search(client, settings)
    finally:
        await dead.disconnect()
    body = r.text
    for value in (
        _DEAD_PASSWORD,
        _DEAD_USER,
        _DEAD_DSN,
        settings.database_url,
        f"{_DEAD_HOST}:{_DEAD_PORT}",
    ):
        assert value not in body


# ── 회귀: 살아 있는 DB 경로를 죽이지 않았는가 ────────────
async def test_healthy_db_still_returns_200(search_api, settings):
    """`acquire()`가 획득·반납을 직접 하도록 바뀌었다. 정상 경로가 그대로 산다."""
    async with search_api(_ok) as (client, seen):
        r = await _post_search(client, settings)
    assert r.status_code == 200 and r.json() == {"results": []}
    assert len(seen) == 1

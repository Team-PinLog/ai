"""검색 경로 전체 — 업스트림 실패가 **어떤 HTTP 상태로 나가는가**.

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
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from app.client.embedding_client import EmbeddingClient
from app.client.retry import RetryPolicy
from app.main import create_app
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
    def factory(handler, **kw):
        return _search_api(db, settings, handler, **kw)

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

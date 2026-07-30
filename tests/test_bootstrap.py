"""부트스트랩 적재 — `app.bootstrap.load_presets` (keyword-preset.md §2).

`/search`·`/context/process` 이전에 반드시 1회 도는 경로인데 기준선 측정에서 line·branch
모두 0% 였다(`S15P11A705-110`). 적재가 조용히 어긋나면 서버는 Preset 없이 뜨거나 잘못된
Profile 로 뜨고, 그 결과는 판정 단계에서야 드러난다.

DB 계약(UPSERT SET 절·Profile 컬럼)은 Testcontainers pgvector 로 검증한다
(integration-tests.md §4.1). 외부 임베딩 호출만 Fake 로 격리하고 호출 횟수를 단언한다
(§4.2). 실제 GMS 는 부르지 않는다.
"""
from __future__ import annotations

import asyncio
import runpy

import asyncpg
import pytest

from app.bootstrap import load_presets
from app.client.embedding_client import preset_embed_text
from app.core.db import Database
from tests.fakes import deterministic_vector

# ── preset_embed_text — 적재와 평가 하네스가 공유하는 입력 구성 ──────────


def test_preset_embed_text_joins_display_description_and_examples():
    text = preset_embed_text(
        {
            "display_name": "친구와",
            "description": "친구나 지인과 함께한 자리",
            "examples": ["오랜만에 모여 수다", "가볍게 한잔"],
        }
    )
    assert text == "친구와. 친구나 지인과 함께한 자리 오랜만에 모여 수다 가볍게 한잔"


def test_preset_embed_text_without_examples_has_no_trailing_space():
    """examples 가 비면 공백이 꼬리에 남는다 — `.strip()` 이 그걸 지우는 계약."""
    text = preset_embed_text({"display_name": "조용한", "description": "차분한 분위기"})
    assert text == "조용한. 차분한 분위기"
    assert text == text.strip()


# ── YAML 로드 ────────────────────────────────────────────────────────────


def test_load_yaml_returns_presets_with_the_fields_upsert_reads():
    """`_UPSERT` 가 읽는 필수 키가 실제 YAML 에 다 있는지 본다.

    id·code·display_name·category·description 은 `preset[...]` 직접 접근이라 누락되면
    적재가 KeyError 로 죽는다. examples·visibility·version 은 `.get()` 기본값이 있다.
    """
    presets = load_presets._load_yaml()

    assert len(presets) > 0
    for preset in presets:
        for key in ("id", "code", "display_name", "category", "description"):
            assert key in preset, f"{preset.get('code')} 에 {key} 가 없다"
    assert len({p["id"] for p in presets}) == len(presets), "id 가 PK 인데 중복이 있다"
    assert len({p["code"] for p in presets}) == len(presets), "code 가 UNIQUE 인데 중복이 있다"


# ── 적재 (실제 DB + Fake 임베딩) ─────────────────────────────────────────


class _RecordingEmbeddingClient:
    """인터페이스 레벨 Fake. 생성자 시그니처는 실제 EmbeddingClient 와 같게 둔다 —
    `load()` 가 settings 를 클라이언트에 어떻게 넘기는지도 단언 대상이기 때문이다."""

    instances: list["_RecordingEmbeddingClient"] = []

    def __init__(self, *, base_url: str, api_key: str, model: str, dimension: int) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.batches: list[list[str]] = []
        _RecordingEmbeddingClient.instances.append(self)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [deterministic_vector(t, self.dimension) for t in texts]


@pytest.fixture
def fake_embedding_client(monkeypatch):
    """`load_presets` 와 runpy 재실행본 양쪽이 보도록 원본 모듈 속성을 갈아 끼운다."""
    _RecordingEmbeddingClient.instances = []
    monkeypatch.setattr(
        "app.client.embedding_client.EmbeddingClient", _RecordingEmbeddingClient
    )
    monkeypatch.setattr(load_presets, "EmbeddingClient", _RecordingEmbeddingClient)
    return _RecordingEmbeddingClient


async def _fetch_presets(conn: asyncpg.Connection) -> list:
    return await conn.fetch(
        "SELECT id, code, display_name, embedding, embedding_profile, visibility, "
        "is_active, version FROM ai.keyword_preset ORDER BY id"
    )


async def test_load_upserts_every_yaml_preset_with_the_server_profile(
    db, conn, settings, fake_embedding_client
):
    expected = load_presets._load_yaml()

    count = await load_presets.load()

    assert count == len(expected)
    rows = await _fetch_presets(conn)
    assert [r["id"] for r in rows] == sorted(p["id"] for p in expected)
    # Profile·is_active 는 YAML 이 아니라 적재가 채운다(keyword-preset.md §2).
    # 이 둘이 어긋나면 keyword_preset_repo.load_active 가 0건을 돌려주고 서버가 기동에
    # 실패한다 — 적재 시점에 고정해야 하는 값이다.
    assert all(r["embedding_profile"] == settings.embedding_profile for r in rows)
    assert all(r["is_active"] for r in rows)
    assert all(
        len(r["embedding"].to_list()) == settings.embedding_dimension for r in rows
    )


async def test_load_embeds_in_one_call_with_settings_derived_client(
    db, settings, fake_embedding_client
):
    """호출 횟수 단언(§4.2). 항목마다 호출하면 25~30배 비용이 되고, 배치 분할은
    클라이언트 내부 책임이라 적재는 `embed()` 를 정확히 한 번 부른다."""
    expected = load_presets._load_yaml()

    await load_presets.load()

    assert len(fake_embedding_client.instances) == 1
    client = fake_embedding_client.instances[0]
    assert len(client.batches) == 1
    assert client.batches[0] == [preset_embed_text(p) for p in expected]
    assert client.base_url == settings.gms_base_url
    assert client.model == settings.embedding_model
    assert client.dimension == settings.embedding_dimension


async def test_load_is_idempotent_and_conflict_updates_drifted_columns(
    db, conn, settings, fake_embedding_client
):
    """멱등(id PK 기준 UPSERT). 두 번째 실행이 손으로 흔든 값을 YAML 로 되돌린다 —
    `ON CONFLICT DO UPDATE` 의 SET 절이 지워지면 이 단언이 깨진다."""
    first = await load_presets.load()
    target = (await _fetch_presets(conn))[0]

    await conn.execute(
        "UPDATE ai.keyword_preset "
        "SET display_name = 'drifted', version = 999, is_active = false, "
        "    embedding_profile = 'stale-profile' "
        "WHERE id = $1",
        target["id"],
    )

    second = await load_presets.load()

    assert second == first
    rows = await _fetch_presets(conn)
    assert len(rows) == first, "두 번째 실행이 행을 늘렸다 — UPSERT 가 아니라 INSERT 다"
    restored = next(r for r in rows if r["id"] == target["id"])
    assert restored["display_name"] == target["display_name"]
    assert restored["version"] == target["version"]
    assert restored["is_active"] is True
    assert restored["embedding_profile"] == settings.embedding_profile


class _TrackingDatabase(Database):
    """connect/disconnect 호출을 세는 것 외에는 진짜 Database 다.

    `pg_stat_activity` 를 세지 않는다 — 풀을 닫아도 서버가 백엔드를 정리하는 시점은
    비동기라 그 대조는 간헐 실패한다. 여기서 지킬 계약은 "적재가 반납을 호출한다"이고,
    그건 값으로 셀 수 있다.
    """

    instances: list["_TrackingDatabase"] = []

    def __init__(self, dsn: str) -> None:
        super().__init__(dsn)
        self.connects = 0
        self.disconnects = 0
        _TrackingDatabase.instances.append(self)

    async def connect(self) -> None:
        await super().connect()
        self.connects += 1

    async def disconnect(self) -> None:
        await super().disconnect()
        self.disconnects += 1


@pytest.fixture
def tracking_database(monkeypatch):
    _TrackingDatabase.instances = []
    monkeypatch.setattr(load_presets, "Database", _TrackingDatabase)
    return _TrackingDatabase


async def test_load_returns_its_pool(db, fake_embedding_client, tracking_database):
    """`finally: await db.disconnect()`. 적재는 짧게 살다 죽는 Job 이라 새는 커넥션이
    바로 보이지 않는다 — 남으면 부트스트랩 Job 이 pod 종료를 붙잡는다."""
    await load_presets.load()

    assert len(tracking_database.instances) == 1
    database = tracking_database.instances[0]
    assert (database.connects, database.disconnects) == (1, 1)


async def test_load_returns_its_pool_even_when_the_upsert_fails(
    db, conn, fake_embedding_client, tracking_database
):
    """실패 경로가 `finally` 의 존재 이유다. 예외는 그대로 올리되 풀은 반납해야 한다.

    차원이 어긋난 벡터를 넣어 UPSERT 를 DB 레벨에서 깨뜨린다 — 적재가 예외를 삼키고
    0건으로 조용히 끝나지 않는 것도 함께 단언한다.
    """
    monkey_dimension = 8  # keyword_preset.embedding 은 VECTOR(1536)
    fake_embedding_client.instances = []
    original_embed = _RecordingEmbeddingClient.embed

    async def _wrong_dimension(self, texts):
        await original_embed(self, texts)
        return [deterministic_vector(t, monkey_dimension) for t in texts]

    _RecordingEmbeddingClient.embed = _wrong_dimension
    try:
        with pytest.raises(asyncpg.PostgresError):
            await load_presets.load()
    finally:
        _RecordingEmbeddingClient.embed = original_embed

    database = tracking_database.instances[0]
    assert (database.connects, database.disconnects) == (1, 1)
    assert await conn.fetchval("SELECT count(*) FROM ai.keyword_preset") == 0


# ── 엔트리포인트 ─────────────────────────────────────────────────────────


def test_main_prints_count_and_does_not_raise(monkeypatch, capsys):
    """`main()` 은 `asyncio.run` 을 자기가 부른다 — 그래서 이 테스트는 sync 다."""
    monkeypatch.setattr(load_presets, "load", _stub_load(7))

    load_presets.main()

    assert capsys.readouterr().out.strip() == "OK: 7 presets upserted"


def _stub_load(count: int):
    async def _load() -> int:
        return count

    return _load


# runpy 는 이미 import 된 모듈을 다시 실행할 때 경고한다. 여기서는 그것이 의도다
# (스크립트 실행 경로를 재현하는 것이 이 테스트의 목적).
@pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
def test_module_runs_as_a_script_end_to_end(clean_dsn, settings, fake_embedding_client, capsys):
    """`python -m app.bootstrap.load_presets` 경로. `if __name__ == "__main__"` 아래는
    import 로는 한 줄도 실행되지 않으므로 `runpy` 로 실제 스크립트 실행을 재현한다.

    `runpy` 는 새 네임스페이스에서 모듈을 다시 실행하므로 캐시된 모듈에 건 패치는 보이지
    않는다. `fake_embedding_client` 가 **원본 모듈**(`app.client.embedding_client`)의 속성을
    갈아 끼우는 이유다 — 새 네임스페이스의 `from ... import EmbeddingClient` 가 그 값을 집는다.
    DB 는 진짜 컨테이너를 쓴다.
    """
    runpy.run_module("app.bootstrap.load_presets", run_name="__main__")

    assert capsys.readouterr().out.strip().startswith("OK: ")
    stored = asyncio.run(_count_presets(clean_dsn))
    assert stored == len(load_presets._load_yaml())


async def _count_presets(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT count(*) FROM ai.keyword_preset")
    finally:
        await conn.close()

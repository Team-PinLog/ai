from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.main import create_app
from app.service.place_suggestion_service import PlaceSuggestionService
from tests.fakes import deterministic_vector


def _preset_row() -> dict:
    return {
        "id": 1,
        "code": "PROBE",
        "display_name": "조용한",
        "category": "MOOD",
        "description": "소음이 적은 분위기",
        "examples": [],
        "visibility": "PUBLIC",
        "version": 1,
        "embedding": deterministic_vector("quiet"),
    }


class FakeDatabase:
    instances: list["FakeDatabase"] = []

    def __init__(self, _dsn: str) -> None:
        self.connected = False
        self.disconnected = False
        self.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    @asynccontextmanager
    async def acquire(self):
        yield object()


class FakeHttpClient:
    instances: list["FakeHttpClient"] = []

    def __init__(self, **_kwargs) -> None:
        self.closed = False
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        self.closed = True


def _patch_runtime(monkeypatch, rows: list[dict]) -> None:
    FakeDatabase.instances.clear()
    FakeHttpClient.instances.clear()
    monkeypatch.setattr("app.main.Database", FakeDatabase)
    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeHttpClient)

    async def load_active(_conn, _profile):
        return rows

    monkeypatch.setattr("app.main.keyword_preset_repo.load_active", load_active)


async def test_lifespan_assembles_place_service_and_closes_resources(monkeypatch):
    _patch_runtime(monkeypatch, [_preset_row()])
    app = create_app()

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.place_suggestion_service, PlaceSuggestionService)
        assert FakeDatabase.instances[0].connected
        assert not FakeHttpClient.instances[0].closed

    assert FakeHttpClient.instances[0].closed
    assert FakeDatabase.instances[0].disconnected


async def test_startup_failure_still_closes_http_and_database(monkeypatch):
    _patch_runtime(monkeypatch, [])
    app = create_app()

    with pytest.raises(RuntimeError, match="Keyword Preset 적재 0건"):
        async with app.router.lifespan_context(app):
            pass

    assert FakeHttpClient.instances[0].closed
    assert FakeDatabase.instances[0].disconnected

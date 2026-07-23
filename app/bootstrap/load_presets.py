"""Keyword Preset 부트스트랩 적재.

data/keyword_preset.yaml의 메타데이터를 읽어 각 항목을 임베딩하고 ai.keyword_preset에
UPSERT한다. embedding/embedding_profile/version은 YAML이 아니라 여기서 채운다
(keyword-preset.md, data/keyword_preset.yaml 헤더 주석).

/search·/context/process 이전에 반드시 1회 실행한다. 멱등(id PK 기준 UPSERT).

    python -m app.bootstrap.load_presets
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from app.client.embedding_client import EmbeddingClient, preset_embed_text
from app.core.config import get_settings
from app.core.db import Database
from app.core.logging import configure_logging, get_logger

log = get_logger("app.bootstrap")

_YAML_PATH = Path(__file__).resolve().parents[2] / "data" / "keyword_preset.yaml"

_UPSERT = """
INSERT INTO ai.keyword_preset
    (id, code, display_name, category, description, examples,
     embedding, embedding_profile, visibility, is_active, version)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (id) DO UPDATE SET
    code = EXCLUDED.code,
    display_name = EXCLUDED.display_name,
    category = EXCLUDED.category,
    description = EXCLUDED.description,
    examples = EXCLUDED.examples,
    embedding = EXCLUDED.embedding,
    embedding_profile = EXCLUDED.embedding_profile,
    visibility = EXCLUDED.visibility,
    is_active = EXCLUDED.is_active,
    version = EXCLUDED.version
"""


def _load_yaml() -> list[dict]:
    data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    return data["presets"]


async def load() -> int:
    settings = get_settings()
    presets = _load_yaml()

    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    vectors = await client.embed([preset_embed_text(p) for p in presets])

    db = Database(settings.database_url)
    await db.connect()
    try:
        async with db.transaction() as conn:
            for preset, vector in zip(presets, vectors):
                await conn.execute(
                    _UPSERT,
                    preset["id"],
                    preset["code"],
                    preset["display_name"],
                    preset["category"],
                    preset["description"],
                    list(preset.get("examples", [])),
                    vector,
                    settings.embedding_profile,
                    preset.get("visibility", "PUBLIC"),
                    True,
                    int(preset.get("version", 1)),
                )
    finally:
        await db.disconnect()

    log.info("upserted %d presets (profile=%s)", len(presets), settings.embedding_profile)
    return len(presets)


def main() -> None:
    configure_logging()
    count = asyncio.run(load())
    print(f"OK: {count} presets upserted")


if __name__ == "__main__":
    main()

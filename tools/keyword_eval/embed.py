"""임베딩 클라이언트 + 공용 유틸.

계약(ai/docs/model-profile.md): text-embedding-3-small / 1536 / cosine.
Profile은 하드코딩하지 않고 PINLOG_EMBEDDING_PROFILE에서 주입받는다.

환경변수 (터미널 또는 tools/keyword_eval/.env):
  GMS_API_KEY   (또는 OPENAI_API_KEY)   : API 키. 필수.
  GMS_BASE_URL  (또는 OPENAI_BASE_URL)  : OpenAI 호환 base URL. 기본 https://api.openai.com/v1
  PINLOG_EMBEDDING_MODEL                : 기본 text-embedding-3-small
  PINLOG_EMBEDDING_PROFILE              : 기본 openai-text-embedding-3-small-1536-cosine-v1

키가 없으면 명확히 실패한다(조용한 Profile 불일치 방지).
결과는 .cache/에 (model, text) 해시로 캐시해 재호출을 막는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
CACHE.mkdir(exist_ok=True)


def _load_dotenv() -> None:
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def config() -> dict:
    _load_dotenv()
    key = os.environ.get("GMS_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit(
            "임베딩 키가 없습니다. GMS_API_KEY(또는 OPENAI_API_KEY)를 환경변수나 "
            f"{HERE/'.env'} 에 설정하세요. (채팅에 키 값을 붙이지 마세요.)"
        )
    base = (
        os.environ.get("GMS_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    model = os.environ.get("PINLOG_EMBEDDING_MODEL", "text-embedding-3-small")
    profile = os.environ.get(
        "PINLOG_EMBEDDING_PROFILE", "openai-text-embedding-3-small-1536-cosine-v1"
    )
    return {"key": key, "base": base, "model": model, "profile": profile}


def _cache_path(model: str, text: str) -> Path:
    h = hashlib.sha256(f"{model}\x00{text}".encode("utf-8")).hexdigest()[:32]
    return CACHE / f"{h}.json"


def embed(texts: list[str], cfg: dict | None = None) -> np.ndarray:
    """texts를 임베딩해 (n, dim) float32 배열 반환. 캐시 우선."""
    cfg = cfg or config()
    model = cfg["model"]
    out: list[list[float] | None] = [None] * len(texts)
    todo: list[tuple[int, str]] = []
    for i, t in enumerate(texts):
        cp = _cache_path(model, t)
        if cp.exists():
            out[i] = json.loads(cp.read_text())
        else:
            todo.append((i, t))

    if todo:
        import httpx

        # OpenAI 호환 /embeddings. 배치 전송.
        for start in range(0, len(todo), 128):
            chunk = todo[start : start + 128]
            resp = httpx.post(
                f"{cfg['base']}/embeddings",
                headers={"Authorization": f"Bearer {cfg['key']}"},
                json={"model": model, "input": [t for _, t in chunk]},
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            for (idx, text), item in zip(chunk, data):
                vec = item["embedding"]
                out[idx] = vec
                _cache_path(model, text).write_text(json.dumps(vec))

    arr = np.asarray(out, dtype=np.float32)
    return arr


def unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.clip(n, 1e-12, None)


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(na, nb) cosine 유사도."""
    return unit(a) @ unit(b).T


def load_presets(path: Path | None = None) -> list[dict]:
    import yaml

    p = path or (HERE.parents[1] / "data" / "keyword_preset.yaml")
    return yaml.safe_load(p.read_text(encoding="utf-8"))["presets"]


def preset_embed_text(p: dict) -> str:
    """프리셋을 임베딩할 때 쓰는 표현. display_name + description + examples.
    LLM 판정 근거와 동일 필드를 벡터에도 반영해 검색-판정 정합을 맞춘다.
    """
    ex = " ".join(p.get("examples", []))
    return f"{p['display_name']}. {p['description']} {ex}".strip()


if __name__ == "__main__":
    cfg = config()
    print("model  :", cfg["model"])
    print("profile:", cfg["profile"])
    print("base   :", cfg["base"])
    v = embed(["연결 테스트"], cfg)
    print("dim    :", v.shape[1])

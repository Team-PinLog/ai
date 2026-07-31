"""GMS 토큰 계측 — 환경변수 게이트, 벤더별 응답 형식 파싱, 실패 삼킴.

`app/client/_usage.py`는 임베딩(OpenAI 호환 `usage`)과 판정 응답에 실려 오던 토큰 수를
기록한다. 그 값을 두 클라이언트가 전부 버리고 있어 비용을 사후에 셀 방법이 없던 것이
신설 이유다(S15P11A705-174).

판정은 벤더마다 필드 이름이 다르고, 폴백 체인이 한 배포 안에서 세 형식을 섞는다
(S15P11A705-175). 벤더별 추출과 실제 client 호출 경로는 `test_llm_vendors.py`가 응답
봉투째로 덮고, 여기서는 **게이트와 행 형식**을 고정한다.

**게이트가 이 모듈의 핵심 계약이다.** `PINLOG_TOKEN_LOG`가 없으면 아무 파일도 만들지
않아야 운영 경로에 영향이 없다. 그 조건이 깨지면 모든 요청이 디스크 쓰기를 하게 되므로
가장 먼저 고정한다.

DB·네트워크가 필요 없다. tmp_path에 쓰고 읽어 확인한다.
"""
from __future__ import annotations

import json

import pytest

from app.client._usage import record

_EMBEDDING_PAYLOAD = {
    "data": [{"index": 0, "embedding": [0.1, 0.2]}],
    "usage": {"prompt_tokens": 57, "total_tokens": 57},
}

_JUDGE_PAYLOAD = {
    "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
    "usageMetadata": {
        "promptTokenCount": 790,
        "candidatesTokenCount": 49,
        "thoughtsTokenCount": 0,
        "totalTokenCount": 839,
    },
}


def _read(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_no_env_writes_nothing(tmp_path, monkeypatch):
    """환경변수가 없으면 파일을 만들지 않는다 — 운영 경로 무영향의 근거."""
    monkeypatch.delenv("PINLOG_TOKEN_LOG", raising=False)
    target = tmp_path / "should-not-exist.jsonl"

    record("embedding", _EMBEDDING_PAYLOAD)
    record("judge", _JUDGE_PAYLOAD, vendor="gemini")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_embedding_usage_recorded(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("PINLOG_TOKEN_LOG", str(log))

    record("embedding", _EMBEDDING_PAYLOAD)

    (row,) = _read(log)
    assert row["kind"] == "embedding"
    assert row["prompt"] == 57
    assert row["total"] == 57
    assert isinstance(row["at"], float)


def test_judge_usage_recorded(tmp_path, monkeypatch):
    """Gemini는 필드명이 다르다 — camelCase에 thoughts가 따로 있다."""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("PINLOG_TOKEN_LOG", str(log))

    record("judge", _JUDGE_PAYLOAD, vendor="gemini", model="gemini-2.5-flash")

    (row,) = _read(log)
    assert row["kind"] == "judge"
    assert row["prompt"] == 790
    assert row["output"] == 49
    assert row["thoughts"] == 0
    assert row["total"] == 839
    assert row["vendor"] == "gemini" and row["model"] == "gemini-2.5-flash"


def test_unknown_judge_vendor_still_records_the_call(tmp_path, monkeypatch):
    """토큰을 못 읽어도 호출 사실은 남긴다 — 횟수는 세어야 한다.

    벤더 이름이 추출표에 없는 상태 자체는 `test_llm_vendors.py`가 레지스트리 대조로
    막는다. 여기서 고정하는 것은 그 상태가 되어도 기록이 죽지 않는다는 것이다.
    """
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("PINLOG_TOKEN_LOG", str(log))

    record("judge", _JUDGE_PAYLOAD, vendor="unknown-vendor", model="m")

    (row,) = _read(log)
    assert row["vendor"] == "unknown-vendor"
    assert (row["prompt"], row["output"], row["total"]) == (None, None, None)


def test_embedding_row_has_no_vendor_field(tmp_path, monkeypatch):
    """임베딩은 폴백 대상이 아니다(프로바이더가 바뀌면 벡터 공간이 달라진다 —
    model-profile.md §3.2). 경로가 하나뿐이므로 행에 벤더를 붙이지 않는다."""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("PINLOG_TOKEN_LOG", str(log))

    record("embedding", _EMBEDDING_PAYLOAD)

    (row,) = _read(log)
    assert "vendor" not in row


def test_appends_not_overwrites(tmp_path, monkeypatch):
    """집계가 성립하려면 호출마다 한 줄씩 쌓여야 한다."""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("PINLOG_TOKEN_LOG", str(log))

    record("embedding", _EMBEDDING_PAYLOAD)
    record("judge", _JUDGE_PAYLOAD, vendor="gemini")
    record("embedding", _EMBEDDING_PAYLOAD)

    rows = _read(log)
    assert [r["kind"] for r in rows] == ["embedding", "judge", "embedding"]


@pytest.mark.parametrize(
    "payload",
    [
        {},                       # usage 자체가 없다
        {"usage": None},          # 있는데 null
        {"usageMetadata": {}},    # 있는데 비었다
    ],
)
def test_missing_usage_is_recorded_as_none(tmp_path, monkeypatch, payload):
    """토큰 정보가 없어도 기록은 남는다 — 호출 횟수는 세어야 하기 때문이다."""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("PINLOG_TOKEN_LOG", str(log))

    record("embedding", payload)

    (row,) = _read(log)
    assert row["total"] is None


def test_write_failure_is_swallowed(tmp_path, monkeypatch):
    """계측이 본 작업을 죽이지 않는다.

    경로가 디렉터리면 open이 실패한다. 그 예외가 새면 임베딩 호출 하나가
    통째로 실패하므로, 조용히 삼키는 것이 옳다.
    """
    blocked = tmp_path / "a-directory"
    blocked.mkdir()
    monkeypatch.setenv("PINLOG_TOKEN_LOG", str(blocked))

    record("embedding", _EMBEDDING_PAYLOAD)  # 예외가 나면 이 테스트가 실패한다

"""`keyword_matrix._parse_vector` — pgvector 반환형 3종을 모두 받는다 (T76 회귀).

pgvector 는 코덱 등록 여부에 따라 반환형이 다르다. `app.core.db.Database` 경유는
`Vector` 객체(iterable 아님, `to_list()` 보유)로, raw asyncpg 는 문자열로 온다.
`Vector` 분기가 빠지면 실측이 TypeError 로 중단된다 — 실제로 그랬다(T76).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "search_cut"))

from keyword_matrix import _parse_vector  # noqa: E402


class _FakeVector:
    """pgvector `Vector` 의 형태 — iterable 이 아니고 `to_list()` 만 있다."""

    def __init__(self, values):
        self._values = tuple(values)

    def to_list(self):
        return list(self._values)


def test_string_form():
    assert _parse_vector("[0.1, 0.2,0.3]") == [0.1, 0.2, 0.3]


def test_vector_object_form():
    assert _parse_vector(_FakeVector((0.1, 0.25))) == [0.1, 0.25]


def test_iterable_form():
    assert _parse_vector([1, 2]) == [1.0, 2.0]

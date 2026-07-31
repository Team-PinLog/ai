"""시딩 preflight의 판정 로직 — **일부러 어긋내 RED를 본다**.

통과만 확인하면 아무것도 검사하지 않는 장치를 놓친다. 세 판정 함수 각각에 대해
"걸려야 하는 입력"을 먼저 두고, 그다음 현재 계약이 통과한다는 것을 본다
(`S15P11A705-156`이 완료 조건에 넣은 것과 같은 이유).

`tools/`는 `app` 커버리지 게이트 범위 밖이지만(§4.2) 이 세 함수는 **DB도 HTTP도
없이 판정이 끝나므로** 통합 계층이 아니라 여기서 검증하는 것이 맞다. 실제 스키마와
대조하는 부분은 `check_write_contract`이며 그것은 실 DB 실행으로 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/demo_seed"))

from preflight import (  # noqa: E402
    DB,
    NULL,
    SEED,
    WRITE_CONTRACT,
    diff_write_contract,
    format_orphans,
    pending_migrations,
)

# `core.social_account`의 V6 이후 실제 모습. `(nullable, has_default)`.
ACTUAL_SOCIAL_ACCOUNT = {
    "id": (False, True),
    "member_id": (False, False),
    "provider": (False, False),
    "provider_user_id": (False, False),
    "email": (False, False),
    "created_at": (False, True),
    "deleted_at": (True, False),
}


class TestWriteContract:
    def test_현재_계약은_실제_스키마와_일치한다(self):
        assert (
            diff_write_contract(
                "core.social_account",
                WRITE_CONTRACT["core.social_account"],
                ACTUAL_SOCIAL_ACCOUNT,
            )
            == []
        )

    def test_back이_컬럼을_추가하면_걸린다(self):
        """이 티켓이 막으려는 재발 경로 그 자체.

        `email`이 V4에서 nullable로 태어났을 때 우리는 알아차리지 못했다. 새 컬럼이
        nullable이어도 — 아니 nullable이라서 — 걸려야 한다. 그때가 아니면 다음
        기회는 back 기동이 죽는 순간이다.
        """
        actual = ACTUAL_SOCIAL_ACCOUNT | {"nickname": (True, False)}
        problems = diff_write_contract(
            "core.social_account", WRITE_CONTRACT["core.social_account"], actual
        )
        assert len(problems) == 1
        assert "nickname" in problems[0]
        assert "계약에 없는 컬럼" in problems[0]

    def test_사고_당시_계약이라면_email에서_걸린다(self):
        """`email`을 선언에서 빼면 — 즉 사고 당시의 인지 상태로 되돌리면 — RED."""
        declared = {
            k: v for k, v in WRITE_CONTRACT["core.social_account"].items() if k != "email"
        }
        problems = diff_write_contract(
            "core.social_account", declared, ACTUAL_SOCIAL_ACCOUNT
        )
        assert len(problems) == 1
        assert "`email`" in problems[0]

    def test_컬럼이_사라지면_걸린다(self):
        actual = {k: v for k, v in ACTUAL_SOCIAL_ACCOUNT.items() if k != "provider_user_id"}
        problems = diff_write_contract(
            "core.social_account", WRITE_CONTRACT["core.social_account"], actual
        )
        assert len(problems) == 1
        assert "실제 스키마에 없는 컬럼" in problems[0]

    @pytest.mark.parametrize("declared_as", [DB, NULL])
    def test_우리가_안_채우는_컬럼에_NOT_NULL이_걸리면_잡힌다(self, declared_as):
        """V6가 `email`에 한 것을 `deleted_at`에 한다면. 기본값이 없으면 INSERT가 죽는다."""
        declared = WRITE_CONTRACT["core.social_account"] | {"deleted_at": declared_as}
        actual = ACTUAL_SOCIAL_ACCOUNT | {"deleted_at": (False, False)}
        problems = diff_write_contract("core.social_account", declared, actual)
        assert len(problems) == 1
        assert "NOT NULL이고 기본값이 없다" in problems[0]

    def test_NOT_NULL이어도_기본값이_있으면_통과한다(self):
        declared = WRITE_CONTRACT["core.social_account"] | {"deleted_at": DB}
        actual = ACTUAL_SOCIAL_ACCOUNT | {"deleted_at": (False, True)}
        assert diff_write_contract("core.social_account", declared, actual) == []

    def test_우리가_채우는_컬럼은_NOT_NULL이어도_통과한다(self):
        assert WRITE_CONTRACT["core.social_account"]["email"] == SEED
        assert (
            diff_write_contract(
                "core.social_account",
                WRITE_CONTRACT["core.social_account"],
                ACTUAL_SOCIAL_ACCOUNT,
            )
            == []
        )


class TestPendingMigrations:
    def test_V6가_미적용이면_이름으로_드러난다(self):
        files = [
            "V1__create_schemas.sql",
            "V4__social_account.sql",
            "V6__social_account_email_not_null.sql",
        ]
        applied = {"V1__create_schemas.sql", "V4__social_account.sql"}
        assert pending_migrations(applied, files) == [
            "V6__social_account_email_not_null.sql"
        ]

    def test_전부_적용됐으면_비어_있다(self):
        files = ["V1__create_schemas.sql", "V4__social_account.sql"]
        assert pending_migrations(set(files), files) == []

    def test_DB에만_있는_마이그레이션은_보고하지_않는다(self):
        """back 브랜치를 되돌린 경우. 우리가 할 수 있는 일이 없으므로 침묵한다."""
        assert pending_migrations({"V9__x.sql"}, ["V1__create_schemas.sql"]) == [
            "V1__create_schemas.sql"
        ]


class TestOrphanReport:
    def test_고아가_없으면_아무_말도_하지_않는다(self):
        assert format_orphans({"ai.context_embedding": 0, "ai.context_keyword": 0}) == []

    def test_고아가_있으면_테이블별로_말하고_지우지_않았다고_말한다(self):
        lines = format_orphans(
            {"ai.context_keyword_analysis": 222, "ai.context_embedding": 0}
        )
        joined = "\n".join(lines)
        assert "222" in joined
        assert "ai.context_keyword_analysis" in joined
        assert "ai.context_embedding" not in joined  # 0인 테이블은 줄을 차지하지 않는다
        assert "지우지 않았다" in joined
        assert "--prune-orphans" in joined
        # 지우면 안 되는 것이 여기 섞여 있다는 사실을 빠뜨리면 보고가 삭제를 부추긴다
        assert "tools/e2e/" in joined


def test_reset과_고아집계가_같은_테이블_목록을_쓴다():
    """결함 2의 재발 방지. 목록이 두 벌이 되는 순간 다시 빠진다.

    `seed.reset()`은 `ORPHAN_TABLES`를 순회해 지우고 `count_orphans()`는 같은
    목록을 센다. 새 `ai.*` 테이블을 목록에 넣지 않으면 reset이 안 지우지만,
    **집계도 그것을 세지 않으므로 조용히 쌓인다** — 그래서 목록 자체가 하나여야
    한다. 이 테스트는 `seed.py`가 자기 목록을 다시 만들지 않았음을 고정한다.
    """
    source = (
        Path(__file__).resolve().parents[1] / "tools/demo_seed/seed.py"
    ).read_text(encoding="utf-8")
    assert "for table in ORPHAN_TABLES" in source
    # 테이블 이름을 직접 나열한 DELETE가 다시 생기면 목록이 갈라진 것이다.
    assert "DELETE FROM ai." not in source

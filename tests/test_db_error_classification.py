"""DB 오류 분류표 — 어떤 예외가 어느 분류로 가는가 (`app/core/db_errors.py`).

`test_api_error_contract.py`의 DB 절이 **한 요청이 관통해 무엇이 나가는가**를 고정한다면,
여기는 **표 자체**를 고정한다. 둘 다 필요하다 — 관통 테스트는 실제 DB로 낼 수 있는
실패만 볼 수 있어서 표의 대부분(`40xxx` 직렬화, `53xxx` 자원, `28xxx` 인증 …)을 덮지
못하고, 표만 보면 그것이 응답이 되는 지점을 또 놓친다(`ai#69`가 정확히 후자였다).

DB를 띄우지 않는다. `asyncpg` 예외 타입을 직접 만들어 분류 함수에 넣는다 —
`classify_db_error`가 던지지 않고 **반환**하도록 만든 이유가 이것이다.
"""
from __future__ import annotations

import socket

import asyncpg
import pytest

from app.core.db_errors import (
    DatabasePermanentError,
    DatabaseTransientError,
    classify_db_error,
)
from app.core.errors import PermanentError, TransientError

# ── 계층: 기존 두 분류의 하위 타입이어야 한다 ────────────
#
# 이것이 깨지면 `main.py`의 핸들러가 받지 못하고 전부 500으로 돌아간다.
# `-220`의 핸들러를 고치지 않고도 503·502가 나가는 근거가 이 두 줄이다.
def test_db_errors_are_subtypes_of_the_two_classifications():
    assert issubclass(DatabaseTransientError, TransientError)
    assert issubclass(DatabasePermanentError, PermanentError)


# ── 일시적: 서버·연결의 상태 때문에 실패했다 ─────────────
_TRANSIENT = [
    # 08xxx — 연결. DB 재기동 중의 질의가 여기로 온다
    asyncpg.ConnectionDoesNotExistError,
    asyncpg.ConnectionFailureError,
    asyncpg.ClientCannotConnectError,
    # 40xxx — 직렬화 실패·교착 (§2.1이 이름으로 지목)
    asyncpg.SerializationError,
    asyncpg.DeadlockDetectedError,
    # 53xxx — 자원. 커넥션 풀 고갈이 서버 쪽에서 이 모양이다
    asyncpg.TooManyConnectionsError,
    asyncpg.OutOfMemoryError,
    # 57xxx — 운영자 개입·재기동·취소
    asyncpg.AdminShutdownError,
    asyncpg.CrashShutdownError,
    asyncpg.CannotConnectNowError,
    asyncpg.QueryCanceledError,
    # 58xxx — 서버 파일 I/O
    asyncpg.PostgresIOError,
    # 잎으로 집은 것들 — 각 군의 나머지는 우리 결함이다
    asyncpg.LockNotAvailableError,  # 55P03 (§2.1이 이름으로 지목)
    asyncpg.IdleInTransactionSessionTimeoutError,  # 25P03
    asyncpg.TransactionTimeoutError,  # 25P04
]


@pytest.mark.parametrize("exc_type", _TRANSIENT, ids=lambda t: t.__name__)
def test_transient_db_errors(exc_type):
    assert isinstance(classify_db_error(exc_type()), DatabaseTransientError)


# ── 영구: 배포 설정이 틀렸다. 재시도해도 같다 ────────────
#
# GMS의 `401`/`403`을 502로 두는 것과 같은 판단이다 — 키·권한 문제는 코드를 고쳐
# 낫지 않고, 그렇다고 "우리 코드의 결함"(500)도 아니다.
_PERMANENT = [
    asyncpg.InvalidPasswordError,  # 28P01
    asyncpg.InvalidAuthorizationSpecificationError,  # 28000
    asyncpg.InvalidCatalogNameError,  # 3D000 — 없는 데이터베이스
    asyncpg.InsufficientPrivilegeError,  # 42501 — GRANT 문제
]


@pytest.mark.parametrize("exc_type", _PERMANENT, ids=lambda t: t.__name__)
def test_permanent_db_errors(exc_type):
    assert isinstance(classify_db_error(exc_type()), DatabasePermanentError)


# ── 미분류: 500에 남는다 ─────────────────────────────────
#
# **이 목록이 이 티켓의 절반이다.** 여기 있는 것을 503으로 감싸면 버그가 숨는다 —
# "일시적으로 사용할 수 없습니다"가 뜨는데 재시도해도 안 되고 알림은 울리지 않는다.
_UNCLASSIFIED = [
    asyncpg.PostgresSyntaxError,  # 42601 — 문법
    asyncpg.UndefinedColumnError,  # 42703 — 없는 컬럼
    asyncpg.UndefinedTableError,  # 42P01 — 없는 테이블
    asyncpg.DatatypeMismatchError,  # 42804 — 타입 불일치
    asyncpg.DataError,  # 22000 — 우리가 보낸 값
    asyncpg.UniqueViolationError,  # 23505 — 제약 위반
    asyncpg.InternalServerError,  # XX000 — 서버 내부. 재시도로 낫지 않는다
    asyncpg.InvalidCachedStatementError,  # 0A000 — asyncpg가 스스로 재시도한다
    ValueError,  # DB와 무관한 예외가 섞여 들어와도 건드리지 않는다
]


@pytest.mark.parametrize("exc_type", _UNCLASSIFIED, ids=lambda t: t.__name__)
def test_unclassified_db_errors_stay_unclassified(exc_type):
    assert classify_db_error(exc_type()) is None
    # 접속 단계에서도 마찬가지다 — `connecting`이 넓히는 것은 `OSError`뿐이다.
    assert classify_db_error(exc_type(), connecting=True) is None


def test_interface_error_is_not_transient_even_though_it_can_mean_closed():
    """`InterfaceError`를 일시 오류로 두지 않는다 — 가장 논쟁적인 판단.

    실측하면 한 타입이 성격이 정반대인 둘을 함께 쓴다.

        connection is closed / pool is closed         수명주기
        the server expects 2 arguments, 1 was passed  우리 결함

    통째로 감싸면 후자가 영구히 숨는다. 그리고 전자는 정상 경로에서 나오지 않는다 —
    풀에서 갓 받은 커넥션이 죽어 있으면 질의는 `08003`으로 끝나고(실측) 그쪽은 이미
    일시 오류로 분류된다. 이 타입이 보인다는 것은 우리가 닫힌 것을 쓰고 있다는 뜻이다.
    """
    assert classify_db_error(asyncpg.InterfaceError("connection is closed")) is None
    assert classify_db_error(asyncpg.InterfaceError("pool is closed")) is None
    assert (
        classify_db_error(
            asyncpg.InterfaceError("the server expects 2 arguments"), connecting=True
        )
        is None
    )


# ── 접속 단계에서만 OSError를 DB 실패로 읽는다 ───────────
#
# 접속 실패는 `asyncpg` 예외가 **아니다**(실측). 이 절이 없으면 이 티켓의 본체인
# "DB 연결 실패"가 통째로 분류 밖에 남는다.
_OS_LEVEL = [
    ConnectionRefusedError,  # 포트가 닫혀 있다
    socket.gaierror,  # DNS 실패
    TimeoutError,  # 접속 타임아웃 · 풀 획득 타임아웃
]


@pytest.mark.parametrize("exc_type", _OS_LEVEL, ids=lambda t: t.__name__)
def test_connect_phase_os_errors_are_transient(exc_type):
    assert isinstance(
        classify_db_error(exc_type(), connecting=True), DatabaseTransientError
    )


@pytest.mark.parametrize("exc_type", _OS_LEVEL, ids=lambda t: t.__name__)
def test_os_errors_outside_the_connect_phase_are_left_alone(exc_type):
    """커넥션을 넘겨준 뒤의 블록에는 호출부의 코드가 함께 들어온다.

    거기서 나온 `OSError`는 DB의 것이 아닐 수 있으므로 번역하지 않는다. 이 구분이
    없으면 무관한 파일·소켓 오류가 "DB가 일시적으로 안 된다"로 둔갑한다.
    """
    assert classify_db_error(exc_type()) is None


def test_pool_acquire_timeout_is_an_os_error_on_this_python():
    """풀 고갈이 일시 오류로 잡히는 근거가 **Python 버전 사실 하나**에 걸려 있다.

    `asyncpg`의 풀 획득 타임아웃은 `asyncio.TimeoutError`이고, 3.11부터 그것이 내장
    `TimeoutError`와 같은 객체이며 `OSError`의 하위 타입이다. 이 관계가 깨지면
    `db_errors.py`의 `OSError` 한 줄이 조용히 풀 고갈을 놓친다.
    """
    import asyncio

    assert asyncio.TimeoutError is TimeoutError
    assert issubclass(TimeoutError, OSError)


# ── 값을 남기지 않는다 ───────────────────────────────────
def test_message_carries_type_and_sqlstate_only():
    """예외 메시지에 원본 메시지를 옮기지 않는다.

    접속 실패 예외에는 host·port가, 드라이버 예외에는 DSN이 섞일 수 있다.
    `probe.py`가 세운 기준(§2.4 원칙 4)을 이 경로에도 적용한다. 대신 SQLSTATE는
    남긴다 — 공개 어휘이고, "풀이 고갈됐다"와 "DB가 재기동 중이다"를 가르는 값이다.
    """
    original = asyncpg.TooManyConnectionsError("host=db.internal user=pinlog password=hunter2")
    translated = classify_db_error(original)
    assert str(translated) == "TooManyConnectionsError[53300]"
    assert "hunter2" not in str(translated)
    assert "db.internal" not in str(translated)


def test_message_without_sqlstate_falls_back_to_the_type_name():
    translated = classify_db_error(
        ConnectionRefusedError("[WinError 1225] 127.0.0.1:5432"), connecting=True
    )
    assert str(translated) == "ConnectionRefusedError"


def test_already_classified_errors_are_not_wrapped_again():
    """`acquire()` 안에서 세션 경계가 중첩돼도 라벨이 겹치지 않는다."""
    assert classify_db_error(DatabaseTransientError("x")) is None
    assert classify_db_error(DatabasePermanentError("x"), connecting=True) is None

"""DB 실패를 `TransientError`/`PermanentError` 로 분류한다.

`errors.py`의 `classify_http_status`가 외부 API 상태 코드에 대해 하는 일을, 이 모듈이
DB 실패에 대해 한다. 분류를 채우기만 하면 `main.py`의 기존 핸들러가 `503`/`502`로
받는다(failure-recovery.md §2.5) — 핸들러는 건드리지 않는다.

## 왜 필요한가

`S15P11A705-220`이 `TransientError→503`·`PermanentError→502` 핸들러를 넣으면서
**500을 「우리 코드의 결함」 전용으로 비워 두는 설계**를 택했다. 그런데 검색 경로의
DB 실패는 아무도 분류하지 않아 그대로 500으로 나갔고, 커넥션 풀 고갈이나 DB 재기동
같은 회복 가능한 상황이 "코드가 깨졌다"와 같은 코드를 쓰고 있었다. 그러면 남은 500이
무엇을 뜻하는지가 다시 흐려진다 — `-220`이 세운 전제가 깨진다.

## 경계 — 어디까지가 일시적인가

**이 경계가 이 모듈의 전부다.** 넓게 잡으면 버그가 503 뒤에 숨는다("일시적으로 사용할
수 없습니다"가 뜨는데 재시도해도 안 되고 알림은 울리지 않는다). 좁게 잡으면 회복
가능한 장애가 알림을 깨운다. 그래서 **`asyncpg` 예외 계층을 실측해**(0.31.0) SQLSTATE
군 단위로 그었고, 애매한 것은 분류하지 않고 500에 남겼다.

기준 한 줄: **서버·연결의 상태 때문에 실패했으면 일시적, 우리가 보낸 질의 때문에
실패했으면 우리 결함이다.**

| 분류 | 대상 | 근거 |
|---|---|---|
| Transient | `OSError` 계열(접속 단계 한정) | 접속 거부·DNS 실패·타임아웃·풀 획득 타임아웃 |
| Transient | `08xxx` `PostgresConnectionError` | 연결이 끊겼다. DB 재기동 중 질의가 이것으로 끝난다 |
| Transient | `40xxx` `TransactionRollbackError` | 직렬화 실패·교착. §2.1이 이름으로 지목한다 |
| Transient | `53xxx` `InsufficientResourcesError` | 커넥션 수 초과·메모리·디스크. 질의가 아니라 자원 |
| Transient | `57xxx` `OperatorInterventionError` | 재기동·기동 중·statement_timeout 취소 |
| Transient | `58xxx` `PostgresSystemError` | 서버 파일 I/O |
| Transient | `55P03` `LockNotAvailableError` | 잠금 타임아웃. §2.1이 이름으로 지목한다 |
| Transient | `25P03`·`25P04` 세션·트랜잭션 타임아웃 | 시간 때문에 끊겼다 |
| Permanent | `28xxx` 인증 실패 | DB 자격 증명. 재시도해도 같다 — GMS `401`을 502로 두는 것과 같은 이유 |
| Permanent | `3D000` 없는 데이터베이스 | 배포 설정 |
| Permanent | `42501` 권한 없음 | GRANT 문제. 코드를 고쳐 낫지 않는다 |
| **미분류(500)** | `42xxx` 문법·없는 컬럼/테이블·타입 불일치 | **우리 질의의 결함** |
| **미분류(500)** | `22xxx` `DataError`, `23xxx` 제약 위반 | 우리가 보낸 값의 결함 |
| **미분류(500)** | `XX000` 서버 내부 오류·손상 | 재시도로 낫지 않고 사람이 봐야 한다 |
| **미분류(500)** | `asyncpg.InterfaceError` | 아래 참조 |

### `InterfaceError`를 분류하지 않는 이유

실측하면 이 한 타입이 **성격이 정반대인 둘**을 함께 쓴다.

```
connection is closed / pool is closed            수명주기
the server expects 2 arguments, 1 was passed     우리 결함
```

통째로 일시 오류로 두면 인자 개수 오류가 503 뒤에 영구히 숨는다. 그리고 수명주기 쪽은
정상 경로에서 나오지 않는다 — 풀에서 갓 받은 커넥션이 죽어 있으면 질의는 `08003`으로
끝나고(실측) 그쪽은 이미 일시 오류다. 이 타입이 보인다는 것은 **우리가 닫힌 것을
쓰고 있다**는 뜻이므로 500이 맞다.

### `OSError`를 접속 단계에서만 번역하는 이유

접속 실패는 `asyncpg` 예외가 **아니다**(실측: `ConnectionRefusedError`,
`socket.gaierror`, `TimeoutError` — 전부 stdlib `OSError`). 풀 획득 타임아웃도
`TimeoutError`이고 Python 3.11부터 그것이 `OSError`의 하위 타입이며
`asyncio.TimeoutError`와 같은 객체다.

그런데 `OSError`는 DB와 무관한 코드도 던진다. 그래서 **커넥션을 얻는 동안에만**
번역하고, 커넥션을 넘겨준 뒤의 블록에서는 `asyncpg` 예외만 번역한다 — 그쪽에서 나온
`OSError`는 우리 것이 아닐 수 있다.

## 값을 남기지 않는다

예외 메시지에 **타입 이름과 SQLSTATE만** 담는다. 원본 메시지를 옮기지 않는다 —
접속 실패 예외에는 host·port가, 드라이버 예외에는 DSN이 섞일 수 있고
[probe.py](../api/probe.py)가 무인증 경로에 세운 "credential·endpoint를 어떤 분기에서도
싣지 않는다"를 이 경로에도 적용한다(§2.4 원칙 4). 원본은 `__cause__`로만 매달아 둔다.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import asyncpg

from app.core.errors import PermanentError, TransientError
from app.core.logging import get_logger

log = get_logger("app.core.db")


class DatabaseTransientError(TransientError):
    """DB가 지금 응답하지 못한다. 기다리면 낫는다 → `503`.

    `TransientError`의 하위 타입인 것이 요점이다 — `main.py`의 기존 핸들러가 그대로
    받으므로 핸들러를 고칠 필요가 없고, 그러면서 핸들러 로그의 `type(exc).__name__`이
    "게이트웨이가 아니라 DB"임을 말해 준다.
    """


class DatabasePermanentError(PermanentError):
    """DB 접속·권한 설정이 틀렸다. 배포 설정을 고쳐야 낫는다 → `502`."""


# SQLSTATE 군 단위. 개별 코드를 나열하지 않는 이유는 군 안에서 성격이 갈리지 않기
# 때문이다 — 갈리는 군(25xxx·55xxx·42xxx)만 아래에서 잎으로 집는다.
_TRANSIENT_PG: tuple[type[BaseException], ...] = (
    asyncpg.PostgresConnectionError,  # 08xxx
    asyncpg.TransactionRollbackError,  # 40xxx
    asyncpg.InsufficientResourcesError,  # 53xxx
    asyncpg.OperatorInterventionError,  # 57xxx
    asyncpg.PostgresSystemError,  # 58xxx
    asyncpg.LockNotAvailableError,  # 55P03 — 55xxx의 나머지는 우리 결함이라 잎으로 집는다
    asyncpg.IdleInTransactionSessionTimeoutError,  # 25P03 — 25xxx도 마찬가지
    asyncpg.TransactionTimeoutError,  # 25P04
)

_PERMANENT_PG: tuple[type[BaseException], ...] = (
    asyncpg.InvalidAuthorizationSpecificationError,  # 28xxx
    asyncpg.InvalidCatalogNameError,  # 3D000
    asyncpg.InsufficientPrivilegeError,  # 42501 — 42xxx의 나머지는 우리 질의의 결함
)


def _label(exc: BaseException) -> str:
    """타입 이름 + SQLSTATE. 원본 메시지는 넣지 않는다(값 노출 금지)."""
    sqlstate = getattr(exc, "sqlstate", None)
    return f"{type(exc).__name__}[{sqlstate}]" if sqlstate else type(exc).__name__


def classify_db_error(
    exc: BaseException, *, connecting: bool = False
) -> DatabaseTransientError | DatabasePermanentError | None:
    """DB 실패를 두 분류 중 하나로 매핑한다. 해당 없으면 `None`.

    `None`을 돌려주는 것이 이 함수의 절반이다 — 호출부가 원본을 그대로 올려 보내고
    응답은 500이 된다. 모르는 것을 5xx로 뭉뚱그리지 않는 것이 `-220`이 500을 비워 둔
    이유다(§2.5).

    `connecting`은 커넥션을 얻는 중인지다. 그때만 `OSError`를 DB 실패로 읽는다.

    예외를 던지지 않고 **반환**한다 — `classify_http_status`와 같은 이유로, 분류 자체를
    DB 없이 단위 테스트할 수 있어야 한다.
    """
    # 이미 분류된 것을 다시 감싸지 않는다. `acquire()`가 중첩될 때 라벨이 겹친다.
    if isinstance(exc, (DatabaseTransientError, DatabasePermanentError)):
        return None

    if isinstance(exc, _PERMANENT_PG):
        return DatabasePermanentError(_label(exc))
    if isinstance(exc, _TRANSIENT_PG):
        return DatabaseTransientError(_label(exc))
    # `TimeoutError`(풀 획득·접속 타임아웃)와 `socket.gaierror`(DNS)가 여기 포함된다.
    if connecting and isinstance(exc, OSError):
        return DatabaseTransientError(_label(exc))
    return None


@contextmanager
def translate_db_errors(*, connecting: bool = False) -> Iterator[None]:
    """블록 안의 DB 실패를 분류 예외로 바꾼다.

    분류가 붙는 순간 로그를 남긴다. 핸들러 로그(`main.py`)는 타입 이름만 찍으므로
    SQLSTATE가 거기 남지 않는데, **"풀이 고갈됐다"와 "DB가 재기동 중이다"를 가르는 것이
    그 값 하나**다. 레벨은 §2.4를 따른다 — 일시 `WARNING`, 영구 `ERROR`(배포 설정
    문제라 알림 대상).
    """
    try:
        yield
    except Exception as exc:
        translated = classify_db_error(exc, connecting=connecting)
        if translated is None:
            raise
        if isinstance(translated, DatabasePermanentError):
            log.error("db permanent failure: %s", translated)
        else:
            log.warning("db transient failure: %s", translated)
        raise translated from exc

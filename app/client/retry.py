"""단일 호출 안의 짧은 재시도 (failure-recovery.md §3.1).

이 재시도는 "네트워크 흔들림 흡수"이지 "복구"가 아니다. 소진되면 `TransientError`가 그대로
올라가 §2.1 경로(상태를 건드리지 않고 PROCESSING으로 둔 채 종료 → Spring 재스캔이 회수)로
넘어간다. FastAPI가 스스로 복구하지 않는 이유는 §1에 있다 — `retry_count`는 DB에 있는
Spring의 것이고, 프로세스 메모리의 재시도 횟수는 프로세스가 죽는 순간 사라진다.

**재시도 대상은 `TransientError` 하나다.** §3.1의 대상 목록(타임아웃·429·5xx·연결 실패)이
§2.1의 Transient 집합과 정확히 같고, 비대상 목록(4xx 요청 오류·인증 실패)이 곧
`PermanentError`다. 그래서 재시도 여부를 판정하는 별도의 상태 코드 표를 두지 않는다 —
분류표와 재시도표를 각자 관리하면 조용히 갈라지고, 실제로 갈라진 것이
S15P11A705-121의 결함 2·3이었다.

백오프 값을 `Settings`(환경변수)로 열지 않는다. 이 값들은 운영이 돌릴 손잡이가 아니라
§3.2의 상한("두 호출의 타임아웃 합 + 재시도 시간 < PROCESSING 만료 600s")에 묶여 있고,
env로 열면 그 상한이 배포마다 달라진다. 대신 `RetryPolicy`를 클라이언트 생성자 인자로 받아
**테스트가 sleep·jitter를 주입**한다 — 실제로 잠드는 테스트를 만들지 않기 위한 설계다.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from app.core.errors import TransientError
from app.core.logging import get_logger

log = get_logger("app.client.retry")

T = TypeVar("T")

Sleep = Callable[[float], Awaitable[None]]
Jitter = Callable[[float], float]


def _full_jitter(delay: float) -> float:
    """[0, delay) 균등 분포(full jitter).

    고정 백오프에 난수를 더하는 방식보다 재시도 시점을 넓게 흩는다. 재스캔이 여러 Context를
    동시에 밀어 넣으므로, 같은 순간 429를 맞은 호출들이 같은 간격 뒤에 재차 몰리는 것을 막는
    것이 목적이다.
    """
    return random.uniform(0.0, delay)


@dataclass(frozen=True)
class RetryPolicy:
    """재시도 횟수·간격과, 그 간격을 실제로 기다리는 수단.

    `attempts`는 **총 시도 횟수**다. §3.1의 "최대 2회 재시도(총 3회 시도)"가 기본값 3에
    해당한다 — 최초 호출 1회 + 재시도 2회.

    `sleep`·`jitter`가 필드인 이유는 테스트다. 기본값은 실제로 잠들고 난수를 쓰지만,
    테스트는 대기 시간을 기록하는 코루틴과 항등 jitter를 주입해 백오프 수열을 값으로
    단언한다(`tests/test_client_retry.py`).
    """

    attempts: int = 3
    base_delay: float = 0.5
    multiplier: float = 2.0
    max_delay: float = 4.0
    jitter: Jitter = _full_jitter
    sleep: Sleep = asyncio.sleep

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts는 1 이상이어야 한다 — 최초 호출 1회를 포함한 총 시도 횟수")

    def _backoff(self, retry_index: int) -> float:
        """jitter 이전의 지수 백오프. `retry_index`는 0-based(첫 재시도가 0)."""
        return min(self.base_delay * self.multiplier**retry_index, self.max_delay)

    def delay_for(self, retry_index: int) -> float:
        """실제 대기 시간. 지수 백오프에 상한을 씌운 뒤 jitter를 적용한다."""
        return self.jitter(self._backoff(retry_index))

    @property
    def worst_case_delay(self) -> float:
        """jitter 상한 기준 총 대기 시간. §3.2의 타임아웃 상한 계산에 쓴다."""
        return sum(self._backoff(i) for i in range(self.attempts - 1))


async def call_with_retry(
    op: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    stage: str,
) -> T:
    """`op`을 최대 `policy.attempts`회 실행한다. `TransientError`만 재시도한다.

    `PermanentError`는 잡지 않는다 — 같은 요청을 다시 보내도 같은 답이 오므로, 재시도는
    대기 시간만 늘리고 §2.2의 FAILED 전이를 늦춘다. 인증 실패라면 그 사이 GMS 호출이
    시도 횟수만큼 늘어나기까지 한다.

    마지막 시도를 루프 밖에 둔다. 그래야 소진 시 `TransientError`가 원래 트레이스백을
    유지한 채 그대로 올라가고, "도달 불가" 분기가 생기지 않는다.
    """
    for retry_index in range(policy.attempts - 1):
        try:
            return await op()
        except TransientError as exc:
            delay = policy.delay_for(retry_index)
            remaining = policy.attempts - retry_index - 1
            # client는 context_id를 모른다(architecture.md §4). 상관 정보는 stage뿐이고,
            # context_id는 이 오류를 받는 service가 §2.1 로그에 남긴다.
            log.warning(
                "stage=%s transient error, retry in %.3fs (%d attempt(s) left): %s",
                stage,
                delay,
                remaining,
                exc,
            )
            await policy.sleep(delay)
    return await op()

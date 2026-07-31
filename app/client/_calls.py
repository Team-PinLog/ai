"""GMS 호출 결과 계측 — 개별 호출 로그와 창 단위 요약.

**무엇을 못 보고 있었나.** dev 의 세 gate 가 전부 열렸는데 GMS 호출이 실패하는지 알
방법이 없었다. GMS 는 공용 게이트웨이라 쿼터가 시점·프로바이더 경로별로 다르다 —
2026-07-29 에 "분당 2건"으로 관측된 것이 다음 날 같은 코드로 분당 30건을 통과시켰다
(S15P11A705-176 실측). 실패율과 지연을 못 보면 시연 중 느려졌을 때 **우리 문제인지
게이트웨이 문제인지 구분할 수 없다.**

`_usage.py` 와 역할이 다르므로 합치지 않았다. 그쪽은 `PINLOG_TOKEN_LOG` 가 있을 때만
동작하는 비용 집계이고 **200 응답을 파싱한 뒤**에 불린다 — 실패한 호출은 한 줄도
남기지 않는다. 실패율의 분자가 애초에 거기 없고, 게이트가 env 라 dev 배포에서는 파일
자체가 만들어지지 않는다. 반대로 이 모듈은 표준 로깅으로만 나가므로 컨테이너 로그
수집에 그대로 걸린다. 하나로 합치면 둘 중 하나의 목적이 반드시 깨진다.

**개별 호출은 결과가 레벨을 가르고, 실패율의 분모는 요약이 센다.** 성공까지 INFO 로
남기면 dev 로그가 GMS 호출로 뒤덮이고, 그렇다고 성공을 아예 안 남기면 실패율을 계산할
분모가 없다. 그래서 성공은 DEBUG·실패는 WARNING 으로 개별 행을 남기고, `_WINDOW_SEC`
마다 집계 한 줄을 INFO 로 낸다. **요약을 밀어내는 것은 타이머가 아니라 다음 호출이다** —
호출이 없으면 요약도 나오지 않으므로 유휴 상태의 로그가 조용하다. 대신 마지막 창은
호출이 끊기면 남지 않아, 종료 시 `flush()` 가 그것을 내보낸다(`app/main.py` lifespan).

**싣지 않는 것**: URL·API 키·요청 본문·응답 본문. `app/api/probe.py` 가 세운 기준
(*"credential·endpoint·profile 값을 어떤 분기에서도 싣지 않는다"*)을 로그에도 적용한다.
요청 본문에는 사용자가 쓴 Context 원문이 들어 있어 더더욱 대상이 아니다. **벤더·모델
이름은 싣는다** — 공개 설정이고 정본이 코드에 있으며(P45), 어느 경로가 막혔는지가 곧
원인이다. 그것을 빼면 이 로그에 남는 값이 없다.

계측이 본 작업을 죽이지 않는다 — 기록 실패는 삼킨다(`_usage.py` 와 같은 규칙).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.core.errors import PermanentError, SchemaViolationError, TransientError
from app.core.logging import get_logger

log = get_logger("app.client.gms")

# 요약 주기. env 로 열지 않는다 — 운영이 돌릴 손잡이가 아니라 "로그가 뒤덮이지 않는 최소
# 간격"이고, 배포마다 달라지면 로그를 읽는 쪽이 창 길이를 가정할 수 없다(retry.py 가
# 백오프를 env 로 열지 않는 것과 같은 이유). 테스트는 `CallMeter(clock=...)` 를 주입한다.
_WINDOW_SEC = 60.0

# 결과 분류. `TransientError`/`PermanentError` 두 분류에 **두 가지를 더 갈라 둔다**.
#   SCHEMA        HTTP 는 200 인데 구조화 출력이 깨진 것. 게이트웨이가 아니라 모델 문제라
#                 처방이 정반대다 — 429 가 많으면 기다리는 것이 답이고, 스키마 위반이
#                 많으면 프롬프트·스키마를 고쳐야 한다. 섞이면 그 판단을 못 한다.
#   UNCLASSIFIED  두 분류 어디에도 안 걸린 예외. 이 값이 로그에 보이면 오류 분류가 새고
#                 있다는 뜻이고(failure-recovery.md §2), 단계가 PROCESSING 에 머문다.
OK = "ok"
TRANSIENT = "transient"
PERMANENT = "permanent"
SCHEMA = "schema"
UNCLASSIFIED = "unclassified"

_LEVEL = {
    OK: logging.DEBUG,
    TRANSIENT: logging.WARNING,
    PERMANENT: logging.WARNING,
    SCHEMA: logging.WARNING,
    UNCLASSIFIED: logging.ERROR,
}

# 요약에서 실패로 세는 분류. 분모는 `calls` 다.
_FAILURES = (TRANSIENT, PERMANENT, SCHEMA, UNCLASSIFIED)


def classify_outcome(exc: BaseException | None) -> str:
    """호출 밖으로 나간 예외를 결과 분류로. `SchemaViolationError` 를 먼저 본다.

    그것이 `TransientError` 의 하위 타입이라(errors.py) 순서를 뒤집으면 스키마 위반이
    전부 transient 로 접혀 SCHEMA 칸이 영영 0 이 된다.
    """
    if exc is None:
        return OK
    if isinstance(exc, SchemaViolationError):
        return SCHEMA
    if isinstance(exc, TransientError):
        return TRANSIENT
    if isinstance(exc, PermanentError):
        return PERMANENT
    return UNCLASSIFIED


@dataclass
class CallRecord:
    """호출 1회에 대해 로그로 나갈 것. `status` 는 호출부가 채운다.

    HTTP 상태 코드이거나, 응답을 받지 못했으면 예외 타입 이름이다(`ConnectTimeout` 등).
    응답 본문은 담지 않는다 — 이 dataclass 에 그 자리를 두지 않는 것이 방어다.
    """

    kind: str                      # "judge" | "embedding"
    model: str
    vendor: str | None = None      # 판정만. 임베딩은 경로가 하나라 없다(_usage.py 와 동일)
    status: object = "-"

    @property
    def route(self) -> str:
        """요약에서 경로별로 접는 키. 벤더 폴백이 어느 칸에서 막혔는지가 여기서 드러난다."""
        return f"{self.kind}:{self.vendor}" if self.vendor else self.kind


class _Window:
    """한 요약 구간의 누적. 창이 끝나면 통째로 버리고 새로 만든다."""

    def __init__(self, started: float) -> None:
        self.started = started
        self.calls = 0
        self.outcomes: Counter[str] = Counter()
        self.routes: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self.elapsed_total = 0.0
        self.elapsed_max = 0.0

    def add(self, rec: CallRecord, elapsed: float, outcome: str) -> None:
        self.calls += 1
        self.outcomes[outcome] += 1
        self.routes[rec.route][outcome] += 1
        self.elapsed_total += elapsed
        self.elapsed_max = max(self.elapsed_max, elapsed)

    def summary(self, now: float) -> str:
        failures = sum(self.outcomes[o] for o in _FAILURES)
        parts = [
            f"window={now - self.started:.0f}s",
            f"calls={self.calls}",
            f"fail={failures}",
            # 정수 백분율로 낸다. 소수점은 이 표본 크기에서 의미가 없고, 로그를 눈으로
            # 훑을 때 자릿수가 흔들리는 편이 더 읽기 나쁘다.
            f"fail_pct={round(100 * failures / self.calls) if self.calls else 0}",
            f"avg_ms={1000 * self.elapsed_total / self.calls:.0f}" if self.calls else "avg_ms=0",
            f"max_ms={1000 * self.elapsed_max:.0f}",
        ]
        for outcome in (OK, *_FAILURES):
            if self.outcomes[outcome]:
                parts.append(f"{outcome}={self.outcomes[outcome]}")
        for route in sorted(self.routes):
            counts = " ".join(
                f"{outcome}={n}" for outcome, n in sorted(self.routes[route].items())
            )
            parts.append(f"[{route} {counts}]")
        return " ".join(parts)


class CallMeter:
    """호출을 감싸 결과·지연을 기록한다. 모듈 전역 인스턴스 `meter` 를 쓴다."""

    def __init__(
        self,
        *,
        window_sec: float = _WINDOW_SEC,
        clock=time.monotonic,
    ) -> None:
        self._window_sec = window_sec
        self._clock = clock
        # asyncio 단일 루프여도 uvicorn 워커가 여럿일 수 있다(_usage.py 와 같은 판단).
        self._lock = threading.Lock()
        self._window: _Window | None = None

    @asynccontextmanager
    async def call(self, kind: str, *, model: str, vendor: str | None = None):
        """호출 1회를 감싼다. 빠져나간 예외가 결과 분류를 정하고, 예외는 그대로 전파된다.

        `yield` 로 받은 `CallRecord` 에 상태 코드를 채우는 것은 호출부 몫이다 — 이 모듈이
        `httpx.Response` 를 알면 응답 본문에 손이 닿는 경로가 생긴다.
        """
        rec = CallRecord(kind=kind, model=model, vendor=vendor)
        started = self._clock()
        try:
            yield rec
        except BaseException as exc:
            self._finish(rec, self._clock() - started, classify_outcome(exc))
            raise
        self._finish(rec, self._clock() - started, OK)

    def flush(self) -> None:
        """진행 중인 창을 즉시 요약으로 내보낸다. 종료 시 마지막 창을 잃지 않기 위한 것."""
        try:
            with self._lock:
                window, self._window = self._window, None
                now = self._clock()
            if window is not None and window.calls:
                log.info("gms window %s", window.summary(now))
        except Exception:  # noqa: BLE001 — 계측이 종료를 막지 않는다
            pass

    def _finish(self, rec: CallRecord, elapsed: float, outcome: str) -> None:
        try:
            log.log(
                _LEVEL[outcome],
                "gms call kind=%s vendor=%s model=%s status=%s outcome=%s ms=%.0f",
                rec.kind,
                rec.vendor or "-",
                rec.model,
                rec.status,
                outcome,
                elapsed * 1000,
            )
            summary = self._accumulate(rec, elapsed, outcome)
            if summary:
                log.info("gms window %s", summary)
        except Exception:  # noqa: BLE001 — 계측이 본 작업을 죽이지 않는다
            pass

    def _accumulate(self, rec: CallRecord, elapsed: float, outcome: str) -> str | None:
        """누적하고, 창이 만료됐으면 그 창의 요약을 돌려준다(그리고 새 창을 연다).

        요약 문자열까지 잠금 안에서 만든다. 밖으로 창 객체를 내보내면 다음 호출이 그것을
        갱신하는 사이에 문자열이 만들어져 집계가 어긋난다.
        """
        now = self._clock()
        with self._lock:
            if self._window is None:
                self._window = _Window(now)
            self._window.add(rec, elapsed, outcome)
            if now - self._window.started < self._window_sec:
                return None
            summary = self._window.summary(now)
            self._window = None
        return summary


meter = CallMeter()

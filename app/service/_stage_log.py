"""단계 전이 중의 관측용 로그. 두 서비스가 같은 문구를 쓰게 한다.

형식이 갈리면 그것을 세는 쪽이 갈린다 — 로그를 `grep` 으로 읽는 동안에는 문구 자체가
계약이다. 그래서 문자열을 서비스마다 두지 않고 여기 한 벌만 둔다.
"""
from __future__ import annotations

from app.core.logging import get_logger

log = get_logger("app.service.stage")


def reclaimed(context_id: int, stage: str, expiry_sec: int) -> None:
    """만료된 stale PROCESSING 을 되찾아 단계를 시작했다.

    **WARNING 이다.** failure-recovery.md §2.3 은 "조건부 UPDATE 영향 행 수 0" 을
    INFO/DEBUG 로 두지만 재선점은 그 표에 없고 정상 경로도 아니다 — 앞선 처리가 만료
    (기본 600s) 안에 끝나지 못했다는 뜻이고, 원인은 프로세스가 죽었거나 GMS 가 그만큼
    느려졌거나 둘뿐이다. 시연 중이라면 어느 쪽이든 즉시 알아야 한다.

    **신규 시작은 남기지 않는다.** 그쪽은 Context 마다 두 번씩 나므로 남기면 dev 로그가
    전이 기록으로 뒤덮이고, 그 안에서 이 한 줄을 찾을 수 없게 된다. 재선점은 드물다.
    """
    log.warning(
        "ctx=%s stage=%s reclaimed stale PROCESSING (expiry=%ds)",
        context_id,
        stage,
        expiry_sec,
    )

"""GMS 양방향 스모크 — embedding 1회 + judge 1회.

    python -m app.smoke.gms_roundtrip

dev 배포 activation 게이트다(ai#32 §3). 배포 후 같은 이미지·같은 env에서 실행해
두 경로가 모두 살아 있음을 증명한다.

**왜 양쪽인가.** `GMS_BASE_URL` 하나를 두 클라이언트가 다르게 소비해서, 잘못된 값이
임베딩만 통과시키고 judge만 죽이는 비대칭 장애가 된다. 형식 검증(`core/config.py`)이
세그먼트 누락은 잡지만 **인증 오류·네트워크 도달 실패·모델 미존재**는 실호출만이
잡는다. 그래서 한쪽이 실패해도 나머지를 건너뛰지 않는다 — 한 번 실행으로 어느 쪽이
죽었는지 알아야 한다.

**DB를 건드리지 않는다.** 판정 후보는 아래 고정 리터럴이라 Preset 적재 여부와 무관하고,
읽기조차 하지 않으므로 부트스트랩 Job 전후 어디서 돌려도 된다.

**출력에 값이 없다.** 검사 이름과 ok/failed, 실패 시 예외 타입 이름뿐이다. 클라이언트의
예외 메시지에는 응답 본문 일부(`resp.text[:200]`)와 요청 URL이 섞여 오므로 메시지를
그대로 흘리지 않는다. 이 명령의 stdout·stderr는 배포 파이프라인 로그에 남는다.

`get_settings()`가 단일 Settings를 강제하므로 실행에는 서버와 동일한 env 전체가
필요하다(부트스트랩 Job과 같은 제약. ai#32 §4).
"""
from __future__ import annotations

import asyncio
import logging
import sys

from app.client.embedding_client import EmbeddingClient
from app.client.llm_client import LLMClient
from app.core.config import Settings, get_settings

# 실호출 1회씩. 임베딩 비용을 아끼려 짧은 한국어 한 문장을 쓴다.
_PROBE_TEXT = "친구와 조용한 카페에서 오래 이야기했다."

# 판정 입력 최소 후보. 실제 Preset을 읽지 않으려 리터럴로 둔다 — 스모크의 관심사는
# 판정 품질이 아니라 왕복 성공 여부다.
_PROBE_CANDIDATES = [
    {
        "id": 1,
        "display_name": "조용한",
        "category": "MOOD",
        "description": "소음이 적고 차분한 분위기",
        "examples": ["조용해서 이야기하기 좋았다"],
    }
]


async def _check_embedding(settings: Settings) -> None:
    """임베딩 1회. 차원 불일치는 클라이언트가 PermanentError로 올린다."""
    client = EmbeddingClient(
        base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    await client.embed_one(_PROBE_TEXT)


async def _check_judge(settings: Settings) -> None:
    """판정 1회.

    선택 결과가 비어 있어도 성공이다. 판정은 비결정적이라 내용을 단언하면 스모크가
    간헐 실패한다 — 여기서 증명할 것은 인증·경로·모델이 살아 있다는 사실뿐이다.
    """
    client = LLMClient(
        gms_base_url=settings.gms_base_url,
        api_key=settings.gms_api_key,
        model=settings.judge_model,
    )
    await client.judge(_PROBE_TEXT, _PROBE_CANDIDATES)


# (이름, 검사) — 순서대로 전부 실행한다. 중간 실패로 끊지 않는다.
_CHECKS = (
    ("embedding", _check_embedding),
    ("judge", _check_judge),
)


async def run_checks(settings: Settings) -> list[tuple[str, str | None]]:
    """각 검사를 1회씩 실행하고 `(이름, 실패 예외 타입 또는 None)`을 모아 반환한다."""
    results: list[tuple[str, str | None]] = []
    for name, check in _CHECKS:
        try:
            await check(settings)
        except Exception as exc:  # noqa: BLE001 — 실패 사유를 값 없이 타입으로만 환원
            results.append((name, type(exc).__name__))
        else:
            results.append((name, None))
    return results


def report(results: list[tuple[str, str | None]]) -> int:
    """결과를 안전한 형태로 출력하고 종료 코드를 반환한다."""
    for name, failure in results:
        status = "ok" if failure is None else f"failed ({failure})"
        print(f"{name}: {status}")

    failed = [name for name, failure in results if failure is not None]
    if failed:
        print(f"SMOKE FAILED: {', '.join(failed)}")
        return 1
    print(f"OK: gms smoke passed ({len(results)} checks)")
    return 0


def _silence_http_logging() -> None:
    """httpx는 요청마다 INFO로 전체 URL을 남긴다. endpoint 노출 경로를 막는다.

    `configure_logging()`을 부르지 않는 이유이기도 하다 — root를 INFO로 열면 그 로그가
    바로 켜진다. 핸들러가 없을 때의 `lastResort`(WARNING+ → stderr)까지 막으려
    레벨을 올려 둔다.
    """
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def main() -> None:
    _silence_http_logging()
    results = asyncio.run(run_checks(get_settings()))
    sys.exit(report(results))


if __name__ == "__main__":
    main()

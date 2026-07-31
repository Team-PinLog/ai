"""로깅 설정."""
from __future__ import annotations

import logging

# httpx 는 요청마다 INFO 로 한 줄을 남기는데, 그 줄에 **요청 URL 전체**가 들어간다.
#
#   INFO httpx HTTP Request: POST https://<gms-host>/gmsapi/generativelanguage.googleapis
#        .com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 429 Too Many Requests"
#
# 두 가지가 걸린다. 첫째, `app/api/probe.py` 가 세운 값 노출 금지(*"credential·endpoint·
# profile 값을 어떤 분기에서도 싣지 않는다"*)를 이 줄이 정면으로 어긴다 — GMS 엔드포인트가
# 배포 로그에 그대로 남는다. 둘째, **성공한 호출까지 INFO 로 남는다.** GMS 호출 로그를
# "성공은 DEBUG·실패는 WARNING"으로 설계해 놓고(`app/client/_calls.py`) 이것을 두면 그
# 설계가 무의미해진다 — 정상 호출이 dev 로그를 뒤덮어 실패 행을 못 찾는 상태 그대로다.
#
# 정보를 잃지 않는다. 같은 호출에 대해 `app.client.gms` 가 더 나은 행을 낸다 —
# 벤더·모델·상태 코드·결과 분류·소요 시간이 있고 URL 은 없다.
_QUIET_LOGGERS = ("httpx",)


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for name in _QUIET_LOGGERS:
        # WARNING 으로 올린다 — 끄지 않는다. httpx 가 경고 이상으로 낼 것이 생기면
        # 그것은 우리 로그가 대신 말해 주지 못하는 내용이다.
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

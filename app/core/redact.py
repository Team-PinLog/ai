"""외부 응답 본문·예외 메시지에서 자격 증명과 endpoint 를 지운다 (S15P11A705-205).

**막는 지점을 로그 호출부가 아니라 문자열이 만들어지는 곳으로 잡았다.** 같은 문자열이
다섯 군데에서 로그가 되고(`retry.py` 1곳 · `embedding_service` 2곳 · `keyword_service`
2곳) 분류 밖 예외는 uvicorn 트레이스백으로도 나간다. 호출부마다 가리면 그 목록을 사람이
세야 하고, 다음에 늘어나는 여섯 번째를 놓친다. 예외 메시지 자체가 깨끗하면 여섯 번째가
어디에 생기든 안전하다.

**지우지 않고 마스킹한다.** 200자를 통째로 버리면 "400 이 왜 났는지"를 담은 유일한 단서를
잃는다(S15P11A705-205 확정 판단). 아래 규칙은 값만 지우고 자리는 남긴다.

## 무엇이 실제로 실려 오는가 — 2026-07-31 실측

세 벤더 경로(OpenAI·Gemini·Anthropic)와 임베딩 경로에 401·모델 미존재·본문 위반을
19건 넣어 응답 본문을 받아 봤다. `docs/implements/2026-07-31-gms-error-body-redaction.md` §1.

    자격 증명   **한 건도 에코되지 않았다.** 401 은 고정 문구다 —
                `{"message":"[GMS 에러] Invalid or expired GMS key","statusCode":401}`
    endpoint    프로바이더 호스트가 맨 호스트 형태로 실린다 —
                `Model not found in request for domain api.openai.com`
    요청 값     OpenAI 가 **앞뒤 3자만 남기고 잘라** 되돌린다 — `Invalid value: 'PIN...def'`

그래서 규칙은 "관측된 것을 지운다"가 아니라 **"되돌아올 수 있는 자리를 막는다"**다. 요청
값이 잘려서라도 round-trip 한다는 것이 실측으로 확인된 이상, 그 자리에 언젠가 자격 증명이
실릴 수 있다고 보는 편이 맞다. `-220` 이 스텁으로 재현한 누출이 정확히 그 모양이었다.

## 규칙 순서

자격 증명이 endpoint 보다 **먼저**다. 순서가 뒤집히면 `https://host/?key=sk-...` 의 키가
URL 규칙에 통째로 삼켜져 "무엇이 지워졌는지"가 로그에서 사라진다 — 지워진 것이 키인지
경로인지는 대응이 갈리는 정보다.
"""
from __future__ import annotations

import re

# 정규식을 훑을 최대 길이. 본문이 병적으로 길 때 마스킹이 요청보다 비싸지지 않게 한다.
# 출력은 이 창 안에서 잘라낸 부분 문자열이므로, 창 밖 내용이 마스킹을 우회해 출력에
# 들어가는 경로는 없다.
_SCAN_LIMIT = 4096

# 예외 메시지에 싣는 본문 길이. `resp.text[:200]` 을 그대로 물려받는다.
_BODY_LIMIT = 200

# TLD 화이트리스트. 맨 호스트(`api.openai.com`)를 지우면서 점이 든 평범한 식별자
# (`messages.0.content` · `contents[0].parts[0]` · `gemini-2.5-flash`)는 건드리지 않으려면
# 마지막 라벨을 한정해야 한다. 우리가 실제로 부르는 호스트를 덮는 최소 집합이다.
_TLD = "com|io|net|org|ai|dev|kr|app|cloud"

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # ── 1. 자격 증명 ──────────────────────────────────────────────────────
    # OpenAI·Anthropic 계열(`sk-`, `sk-ant-`). 접두사는 남긴다 — 어느 종류의 키가
    # 문제였는지가 재발급 대상을 가른다.
    (re.compile(r"\bsk-[A-Za-z0-9_-]{4,}"), "sk-***"),
    # Google API 키.
    (re.compile(r"\bAIza[A-Za-z0-9_-]{6,}"), "AIza***"),
    # JWT. 세그먼트 둘만 있어도 잡는다.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?"), "eyJ***"),
    # Authorization 헤더 값이 본문에 되돌아오는 경우.
    (re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{4,}", re.IGNORECASE), r"\1 ***"),
    # `"apiKey": "..."` · `x-api-key=...` 형태. 키 **이름**은 남기고 값만 지운다.
    (
        re.compile(
            r"(\"?(?:x-)?api[_-]?key\"?|\"?access[_-]?token\"?|\"?authorization\"?"
            r"|\"?secret\"?|\"?password\"?)(\s*[:=]\s*\"?)([^\"\s,}&]{4,})",
            re.IGNORECASE,
        ),
        r"\1\2***",
    ),
    # ── 2. endpoint ───────────────────────────────────────────────────────
    # URL 전체. `-197` 이 httpx 로거에서 막은 것과 같은 형태가 예외 메시지로도 온다.
    (re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>)\]]+"), "<url>"),
    # 맨 호스트. 실측한 GMS 400 문구(`... for domain api.openai.com`)가 이 모양이다.
    # 벤더가 무엇이었는지는 `app.client.gms` 계측의 `vendor=` 가 이미 말해 주므로
    # 이 자리를 지워도 진단이 줄지 않는다.
    (
        re.compile(rf"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+(?:{_TLD})\b"),
        "<host>",
    ),
)


def redact(text: str) -> str:
    """자격 증명·endpoint 를 마스킹한다. 그 밖의 진단 문구는 그대로 둔다."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_body(text: str, limit: int = _BODY_LIMIT) -> str:
    """응답 본문을 예외 메시지에 실을 수 있는 형태로 — **마스킹 뒤에 절단한다.**

    순서가 이 함수의 전부다. 절단을 먼저 하면 200자 경계에 걸친 키의 앞부분이 잘린 채
    남고, 잘린 자격 증명은 여전히 자격 증명이다(OpenAI 가 값을 잘라서 에코한다는 실측이
    바로 그 이야기다).
    """
    return redact(text[:_SCAN_LIMIT])[:limit]

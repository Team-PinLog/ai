"""판정 LLM 벤더 어댑터 — 요청 생성과 응답 파싱을 갈아끼우는 이음새.

**왜 벤더를 갈아끼우는가.** 2026-07-30 실측(`tools/keyword_eval/probe_vendors.py`)에서
같은 시각·같은 GMS 키로 같은 판정 작업을 던졌을 때 Gemini 경로만 429를 냈고
OpenAI·Anthropic 경로는 한 번도 막히지 않았다(성공률 92% / 100% / 100%). 임베딩
(OpenAI 호환 경로) 49회가 안 막힌 것과 같은 그림이다 — **쿼터가 게이트웨이 전역이 아니라
프로바이더 경로별로 걸린다.** 그래서 폴백은 "다른 모델"이 아니라 **다른 경로**여야 하고,
경로마다 인증 헤더·구조화 출력 방식·응답 봉투가 전부 다르다. 그 차이를 여기서 흡수한다.

    OpenAI      {root}/api.openai.com/v1/chat/completions
                Authorization: Bearer <key>      · response_format json_schema(strict)
    Gemini      {root}/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
                x-goog-api-key: <key>            · responseSchema + thinkingBudget=0
    Anthropic   {root}/api.anthropic.com/v1/messages
                x-api-key + anthropic-version    · tools + tool_choice(강제 호출)

키는 셋 다 같은 `GMS_API_KEY`다. 헤더 이름만 다르다.

작동이 확인된 요청·응답 형태는 프로브에서 옮겼다. **다만 스키마는 그대로가 아니다** —
프로브는 `keywordId` 하나만 요구하는 축약본으로 속도·일치도만 재는 도구였고, 운영은
`confidence`와 `unmatchedConcepts`까지 받아야 한다(keyword-preset.md §4.2·§5.2). 그
운영 스키마를 세 벤더의 표현으로 각각 옮긴 것이 아래 세 상수다.

각 어댑터는 **선택 객체(dict)까지만** 돌려주고 `JudgeResult` 변환은 하지 않는다. 후보 밖
폐기·confidence 범위 검사는 벤더와 무관한 규칙이므로 `llm_client`·`keyword_service`에
한 벌만 둔다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from app.core.errors import SchemaViolationError

_MAX_OUTPUT_TOKENS = 2048

# 응답 봉투를 벗기다 터질 수 있는 것들. `json.JSONDecodeError`는 `ValueError` 하위,
# tool_use 블록 부재는 `StopIteration`으로 온다. 하나라도 빠지면 분류되지 않은 예외가
# service까지 새어 단계가 PROCESSING에 머문다.
_ENVELOPE_ERRORS = (
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    StopIteration,
    ValueError,
)

# ── 구조화 출력 스키마 ────────────────────────────────────────────────────
# keywordId enum은 두지 않는다. 후보 밖 값은 매핑 단계에서 폐기한다(keyword-preset.md §4.3).

_ITEM_PROPERTIES = {
    "keywordId": {"type": "integer"},
    "confidence": {"type": "number"},
}
_UNMATCHED = {"type": "array", "items": {"type": "string"}}

# Gemini responseSchema (OpenAPI 부분집합). `unmatchedConcepts`는 required가 아니다 —
# 없으면 빈 배열로 읽는다.
GEMINI_SCHEMA = {
    "type": "object",
    "required": ["selected"],
    "properties": {
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["keywordId", "confidence"],
                "properties": _ITEM_PROPERTIES,
            },
        },
        "unmatchedConcepts": _UNMATCHED,
    },
}

# OpenAI json_schema(strict). strict 모드는 **모든 객체에 additionalProperties: false**와
# **모든 property를 required에 열거**할 것을 요구한다. 하나라도 빠지면 400이고, 400은
# 영구 오류라 폴백 없이 판정이 죽는다 — Gemini 스키마를 그대로 재사용할 수 없는 이유다.
# 그래서 `unmatchedConcepts`도 required에 들어간다(모델은 빈 배열을 낸다).
OPENAI_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["selected", "unmatchedConcepts"],
    "properties": {
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["keywordId", "confidence"],
                "properties": _ITEM_PROPERTIES,
            },
        },
        "unmatchedConcepts": _UNMATCHED,
    },
}

# Anthropic tool input_schema — 일반 JSON Schema다. strict 제약이 없어 Gemini 쪽과 같은
# 모양을 쓴다. 구조화 출력을 responseFormat이 아니라 **도구 강제 호출**로 얻는다.
ANTHROPIC_SCHEMA = GEMINI_SCHEMA


def _unwrap(extract: Callable[[dict], object], payload: dict, vendor: str) -> dict:
    """봉투 벗기기를 `SchemaViolationError`로 환원한다.

    구조화 출력 위반은 재시도 대상이되 소진 후 영구 오류다(failure-recovery.md §2.2).
    폴백 체인에서는 **다음 벤더로 넘어갈 사유**이기도 하다 — 구조화 출력 방식이 벤더마다
    다르므로 한쪽이 절단·차단으로 깨져도 다른 쪽은 성공할 수 있다.
    """
    try:
        data = extract(payload)
    except _ENVELOPE_ERRORS as exc:
        raise SchemaViolationError(f"{vendor} envelope parse failed: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaViolationError(f"{vendor} selection is not an object: {type(data).__name__}")
    return data


class VendorAdapter(Protocol):
    """한 프로바이더 경로에 대한 요청 생성 + 응답 봉투 해석."""

    vendor: str

    def request(
        self, root: str, api_key: str, model: str, system: str, user: str
    ) -> tuple[str, dict[str, str], dict]:
        """(url, headers, body)를 만든다."""

    def parse(self, payload: dict) -> dict:
        """응답 봉투에서 선택 객체(`{"selected": [...], "unmatchedConcepts": [...]}`)를 꺼낸다."""


class OpenAIAdapter:
    vendor = "openai"

    def request(self, root, api_key, model, system, user):
        return (
            f"{root}/api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "keyword_selection",
                        "strict": True,
                        "schema": OPENAI_SCHEMA,
                    },
                },
                "max_completion_tokens": _MAX_OUTPUT_TOKENS,
            },
        )

    def parse(self, payload):
        return _unwrap(
            lambda p: json.loads(p["choices"][0]["message"]["content"]), payload, self.vendor
        )


class GeminiAdapter:
    vendor = "gemini"

    def request(self, root, api_key, model, system, user):
        return (
            f"{root}/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            {"x-goog-api-key": api_key, "content-type": "application/json"},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": GEMINI_SCHEMA,
                    "maxOutputTokens": _MAX_OUTPUT_TOKENS,
                    # thinking을 끈다. 켜면 판정 지연과 토큰이 늘고, 2.5-flash에서
                    # function-calling이 코드형 호출로 malformed 된다(테스트 C-2).
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
        )

    def parse(self, payload):
        return _unwrap(
            lambda p: json.loads(p["candidates"][0]["content"]["parts"][0]["text"]),
            payload,
            self.vendor,
        )


class AnthropicAdapter:
    vendor = "anthropic"

    # 도구 이름은 응답에서 블록을 찾는 열쇠가 아니다(타입으로 찾는다). 그래도 고정해 둔다 —
    # 요청과 응답 양쪽 로그에서 같은 이름으로 보이는 편이 추적에 낫다.
    _TOOL = "select_keywords"

    def request(self, root, api_key, model, system, user):
        return (
            f"{root}/api.anthropic.com/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            {
                "model": model,
                "max_tokens": _MAX_OUTPUT_TOKENS,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "tools": [
                    {
                        "name": self._TOOL,
                        "description": "고른 Keyword 를 보고한다.",
                        "input_schema": ANTHROPIC_SCHEMA,
                    }
                ],
                # 강제 호출. 이것이 없으면 모델이 산문으로 답할 수 있고, 그러면 매 호출이
                # 스키마 위반이 된다.
                "tool_choice": {"type": "tool", "name": self._TOOL},
            },
        )

    def parse(self, payload):
        # tool_use 블록의 input은 이미 객체다 — json.loads 대상이 아니다.
        return _unwrap(
            lambda p: next(b for b in p["content"] if b.get("type") == "tool_use")["input"],
            payload,
            self.vendor,
        )


# 어댑터는 상태가 없으므로 벤더당 하나만 만든다.
ADAPTERS: dict[str, VendorAdapter] = {
    adapter.vendor: adapter
    for adapter in (OpenAIAdapter(), GeminiAdapter(), AnthropicAdapter())
}


@dataclass(frozen=True)
class VendorCall:
    """"이 어댑터로 이 모델을 부른다" — 폴백 체인의 한 칸."""

    adapter: VendorAdapter
    model: str

    @property
    def label(self) -> str:
        """오류 메시지·로그에 쓰는 식별자. 모델명은 공개 설정이므로 값 노출 제약이 없다(P45)."""
        return f"{self.adapter.vendor}:{self.model}"


def resolve_chain(chain: Sequence[tuple[str, str]]) -> tuple[VendorCall, ...]:
    """`(vendor, model)` 목록을 어댑터에 결합한다. 지원하지 않는 벤더면 `ValueError`.

    형식 검증(`vendor:model` 모양)은 `core/config.py`가 하고, **지원 여부**는 여기서 본다 —
    어떤 벤더를 지원하는지 아는 것은 어댑터 레지스트리뿐이다. 둘 다 기동 시점에 터지므로
    잘못된 체인을 들고 뜨는 상태는 성립하지 않는다.
    """
    if not chain:
        raise ValueError("판정 벤더 체인이 비어 있다 — 최소 한 벤더가 필요하다")
    calls = []
    for vendor, model in chain:
        adapter = ADAPTERS.get(vendor)
        if adapter is None:
            raise ValueError(
                f"지원하지 않는 판정 벤더 '{vendor}' — 지원: {', '.join(sorted(ADAPTERS))}"
            )
        calls.append(VendorCall(adapter=adapter, model=model))
    return tuple(calls)

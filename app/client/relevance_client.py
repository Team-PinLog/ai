"""검색 결과 LLM 관련도 재판정 클라이언트 (4번째 검색 신호).

검색 세 신호(재작성·재정렬·문자열 병합)를 전부 거친 최종 후보 목록도 벡터 유사도의
사각지대를 완전히 없애지 못한다 — 질의 문자열이 본문에 그대로 있어도, 다른 후보의
전체적인 "분위기"가 임베딩상 더 가까우면 그 후보가 밀린다. 세 신호 모두 이 실패를
놓친다: 재작성은 짧은 질의에만, 문자열 검색은 단어형 질의에만 켜지고, 재정렬은 미리
정의된 Preset 목록에 없는 개념(예: 소속 기관명)은 신호로 쓸 수 없다.

이 클라이언트는 최종 후보 목록 전체를 LLM 에게 한 번에 보여주고 관련도 4단계로
재판정받는다. 후보당 별도 호출이 아니라 **한 번의 호출로 전체 목록을 판정**한다 —
질의당 N회 호출은 지연·비용이 후보 수에 비례해 늘어난다.

## 왜 back 이 이 클라이언트의 소비자가 아니라 ai 가 새 엔드포인트로 노출하는가

LLM 호출 인프라(벤더 폴백 체인·구조화 출력 파싱·재시도 정책)는 ai 에만 있다. 본문은
back 소유이므로, back 이 최종 후보(본문 포함)를 조립해 이 엔드포인트에 보내고 판정만
받아온다 — 처리 방향은 back→ai 요청이며, "FastAPI 는 조회 응답에 본문을 싣지 않는다"는
기존 계약(back→ai **응답**에 대한 것)과 방향이 달라 저촉되지 않는다.

## 판정 클라이언트·재작성 클라이언트와 무엇이 다른가

`llm_client.py`(키워드 판정)의 후보는 Preset 이고, 이 클라이언트의 후보는 검색 결과
기록이다 — 모양이 달라 `vendors.py`의 request 빌더를 공유하지 않는다(`rewrite_client.py`와
같은 이유). 재작성처럼 **사용자가 기다리는 동기 경로**라 예산은 검색 전용값을 쓰고
판정(백그라운드, 90s·3회)의 값을 상속하지 않는다.

캐시를 두지 않는다 — 입력(질의 + 후보 전체 목록)이 매 검색마다 사실상 달라, 캐시
적중을 기대하기 어렵고 오히려 오래된 후보 조합을 잘못 재사용할 위험만 생긴다.
"""
from __future__ import annotations

import itertools
import json
from typing import Sequence

import httpx

from app.client._calls import meter
from app.client._usage import record as record_usage
from app.client.retry import RetryPolicy, call_with_retry
from app.client.vendors import resolve_chain
from app.core.errors import (
    PermanentError,
    SchemaViolationError,
    TransientError,
    classify_http_status,
)
from app.core.redact import redact, redact_body

_MAX_OUTPUT_TOKENS = 1024

_LABELS = ("VERY_RELEVANT", "RELEVANT", "WEAKLY_RELEVANT", "NOT_RELEVANT")

SYSTEM = (
    "당신은 장소 기록 검색 서비스의 결과 재판정기입니다.\n"
    "사용자의 검색어와, 벡터 유사도로 이미 골라진 후보 기록 목록이 주어집니다.\n"
    "각 후보가 검색어와 실제로 얼마나 관련 있는지 판정하세요.\n"
    "규칙:\n"
    "- 후보 목록에 있는 contextId 전부에 대해 정확히 하나씩 판정합니다. "
    "빠뜨리거나 목록에 없는 contextId를 만들지 마세요.\n"
    "- 판정은 반드시 다음 네 값 중 하나입니다: "
    "VERY_RELEVANT, RELEVANT, WEAKLY_RELEVANT, NOT_RELEVANT.\n"
    "- 검색어의 핵심 단어(고유명사·기관명·인명 등)가 본문에 그대로 있으면 "
    "그 사실을 강하게 반영하세요 — 벡터 유사도가 낮아도 문자 그대로 일치하는 근거는 "
    "관련성의 강한 증거입니다.\n"
    "- 검색어의 전체적인 분위기만 비슷하고 핵심 단어·사실 관계가 다르면 낮게 판정하세요.\n"
    "- placeName과 body만 근거로 삼습니다. 목록에 없는 정보를 지어내지 마세요."
)

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "contextId": {"type": "integer"},
        "relevance": {"type": "string", "enum": list(_LABELS)},
    },
    "required": ["contextId", "relevance"],
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {"type": "array", "items": _ITEM_SCHEMA},
    },
    "required": ["results"],
    "additionalProperties": False,
}

_OPENAI_ITEM_SCHEMA = {**_ITEM_SCHEMA, "additionalProperties": False}
_OPENAI_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {"type": "array", "items": _OPENAI_ITEM_SCHEMA},
    },
    "required": ["results"],
    "additionalProperties": False,
}


def build_user(query: str, candidates: list[dict]) -> str:
    lines = [
        f"- contextId={c['contextId']} | {c['placeName']} | 본문: {c['body']}"
        for c in candidates
    ]
    return f"[검색어]\n{query}\n\n[후보 기록]\n" + "\n".join(lines)


def _openai_request(root: str, key: str, model: str, query: str, candidates: list[dict]):
    return (
        f"{root}/api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "content-type": "application/json"},
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_user(query, candidates)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "relevance_judgment",
                    "strict": True,
                    "schema": _OPENAI_SCHEMA,
                },
            },
            "max_completion_tokens": _MAX_OUTPUT_TOKENS,
        },
    )


def _openai_parse(payload: dict) -> dict:
    return json.loads(payload["choices"][0]["message"]["content"])


def _gemini_request(root: str, key: str, model: str, query: str, candidates: list[dict]):
    return (
        f"{root}/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": key, "content-type": "application/json"},
        {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [
                {"role": "user", "parts": [{"text": build_user(query, candidates)}]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _SCHEMA,
                "maxOutputTokens": _MAX_OUTPUT_TOKENS,
                # 판정·재작성과 같은 이유 — thinking 이 지연·토큰을 늘린다(테스트 C-2).
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
    )


def _gemini_parse(payload: dict) -> dict:
    return json.loads(payload["candidates"][0]["content"]["parts"][0]["text"])


def _anthropic_request(root: str, key: str, model: str, query: str, candidates: list[dict]):
    return (
        f"{root}/api.anthropic.com/v1/messages",
        {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        {
            "model": model,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": build_user(query, candidates)}],
            "tools": [
                {
                    "name": "judge_relevance",
                    "description": "후보 기록의 관련도 판정을 보고한다.",
                    "input_schema": _SCHEMA,
                }
            ],
            # 판정·재작성과 같은 이유 — 강제하지 않으면 산문 응답이 스키마 위반이 된다.
            "tool_choice": {"type": "tool", "name": "judge_relevance"},
        },
    )


def _anthropic_parse(payload: dict) -> dict:
    for block in payload.get("content", []):
        if block.get("type") == "tool_use":
            return block["input"]
    raise KeyError("tool_use block not found")


_BUILDERS = {
    "openai": (_openai_request, _openai_parse),
    "gemini": (_gemini_request, _gemini_parse),
    "anthropic": (_anthropic_request, _anthropic_parse),
}


class RelevanceJudgeClient:
    def __init__(
        self,
        gms_base_url: str,
        api_key: str,
        chain: Sequence[tuple[str, str]],
        *,
        timeout: float,
        retry: RetryPolicy,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._root = gms_base_url.split("/gmsapi/")[0] + "/gmsapi"
        self._key = api_key
        self._chain = [(c.adapter.vendor, c.model) for c in resolve_chain(chain)]
        self._timeout = timeout
        self._retry = retry
        self._transport = transport

    def _for(self, attempt: int) -> tuple[str, str]:
        return self._chain[min(attempt, len(self._chain) - 1)]

    async def judge(self, query: str, candidates: list[dict]) -> list[dict]:
        """후보 전체를 한 번에 판정한다. 실패는 오류로 던진다 — 강등은 호출자 몫이다.

        빠지거나 목록 밖 contextId 를 반환하는 것은 스키마 위반으로 취급하지
        않는다 — 모델이 일부를 놓쳐도 나머지 판정은 쓸모 있고, 놓친 것은 호출자가
        "판정 없음"으로 처리하면 된다(back 이 원래 자리를 유지하는 강등 규칙).
        요청한 contextId 목록 밖의 값이 섞여 있으면 그 항목만 버린다.
        """
        requested = {c["contextId"] for c in candidates}
        attempts = itertools.count()
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:

            async def attempt() -> list[dict]:
                return await self._once(
                    client, self._for(next(attempts)), query, candidates
                )

            try:
                results = await call_with_retry(attempt, self._retry, stage="relevance")
            except SchemaViolationError as exc:
                raise PermanentError(
                    f"relevance judge schema violation after "
                    f"{self._retry.attempts} attempt(s): {exc}"
                ) from exc

        return [r for r in results if r["contextId"] in requested]

    async def _once(
        self,
        client: httpx.AsyncClient,
        call: tuple[str, str],
        query: str,
        candidates: list[dict],
    ) -> list[dict]:
        vendor, model = call
        build, parse = _BUILDERS[vendor]
        url, headers, body = build(self._root, self._key, model, query, candidates)
        async with meter.call("relevance", model=model, vendor=vendor) as rec:
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                rec.status = type(exc).__name__
                raise TransientError(
                    f"relevance judge request failed ({vendor}:{model}): "
                    f"{redact(str(exc))}"
                ) from exc

            rec.status = resp.status_code
            if resp.status_code != 200:
                raise classify_http_status(
                    resp.status_code,
                    f"relevance judge error: {vendor}:{model} {resp.status_code} "
                    f"{redact_body(resp.text)}",
                )

            try:
                payload = resp.json()
                record_usage("relevance", payload, vendor=vendor, model=model)
                data = parse(payload)
                items = data["results"]
                return [
                    {
                        "contextId": int(item["contextId"]),
                        "relevance": str(item["relevance"]),
                    }
                    for item in items
                    if str(item.get("relevance")) in _LABELS
                ]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise SchemaViolationError(
                    f"relevance judge parse failed ({vendor}:{model}): {exc}"
                ) from exc

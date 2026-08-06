"""검색 질의 재작성 클라이언트 (S15P11A705-337, P49 §3).

검색 요청이 임베딩되기 전에 LLM 이 질의를 한 번 다듬는다 — 줄임말을 풀고(`부캠`→`부트캠프`)
붙여쓰기를 편다. 임베딩 모델이 의미 연결을 못 하는 약어 실패의 처방이며, 근거 측정은
`docs/implements/2026-08-05-short-query-boundary.md` 와 P49 §2 에 있다.

## 판정 클라이언트와 무엇이 다른가

`llm_client.py` 의 판정은 백그라운드 경로라 시도 예산이 길다(타임아웃 90s · 총 3회).
재작성은 **사용자가 기다리는 동기 경로**라 예산이 짧다 — 타임아웃·시도 횟수를 검색 전용
설정으로 받고 판정의 값을 상속하지 않는다(티켓 명시). 실패의 의미도 다르다 — 판정 실패는
상태머신에 기록되지만, **재작성 실패는 호출자(SearchService)가 원문으로 되돌리는 강등**이고
오류로 번지지 않는다.

벤더 어댑터를 `vendors.py` 와 공유하지 않는 이유: 그쪽 request 빌더는 키워드 판정의
구조화 출력 스키마를 몸체에 박고 있다. 재작성의 스키마는 `{"query": string}` 하나라
빌더를 이 파일에 따로 둔다 — 벤더별 함정(Gemini thinking 끄기 · Anthropic tool 강제)은
그쪽 실측(테스트 C-2)을 그대로 따른다.

## 캐시

같은 질의는 같은 재작성을 돌려준다. LLM 이 비결정적이라 캐시가 없으면 같은 검색이
회차마다 다른 결과를 낼 수 있다(P48 §3 2단계가 요구한 성질). 프로세스 메모리 FIFO 이며
상한을 넘으면 오래된 것부터 버린다 — 시연 규모에서는 사실상 전부 남는다.
"""
from __future__ import annotations

import itertools
import json
from collections import OrderedDict
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

_MAX_OUTPUT_TOKENS = 128

SYSTEM = (
    "당신은 장소 기록 검색 서비스의 질의 정규화기입니다.\n"
    "사용자의 검색어를 저장된 기록의 표현에 가깝게 한 줄로 다시 씁니다.\n"
    "규칙:\n"
    "- 줄임말·약어는 원래 말로 풀어 씁니다. 예: 부캠 → 부트캠프.\n"
    "- 붙여 쓴 말은 자연스러운 띄어쓰기로 고칩니다.\n"
    "- 뜻을 더하거나 빼지 않습니다. 검색어에 없던 개념을 추가하지 마세요.\n"
    "- 고유명사나 가게 이름으로 보이면 그대로 둡니다.\n"
    "- 확신이 없으면 원문을 그대로 반환합니다.\n"
    '- 결과는 JSON {"query": "다시 쓴 검색어"} 하나만 반환합니다.'
)

_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


def _openai_request(root: str, key: str, model: str, query: str):
    return (
        f"{root}/api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "content-type": "application/json"},
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": query},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "query_rewrite",
                    "strict": True,
                    "schema": _SCHEMA,
                },
            },
            "max_completion_tokens": _MAX_OUTPUT_TOKENS,
        },
    )


def _openai_parse(payload: dict) -> dict:
    return json.loads(payload["choices"][0]["message"]["content"])


def _gemini_request(root: str, key: str, model: str, query: str):
    return (
        f"{root}/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": key, "content-type": "application/json"},
        {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": query}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "maxOutputTokens": _MAX_OUTPUT_TOKENS,
                # 판정 쪽 실측(테스트 C-2)과 같은 이유 — thinking 이 지연·토큰을 늘린다.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
    )


def _gemini_parse(payload: dict) -> dict:
    return json.loads(payload["candidates"][0]["content"]["parts"][0]["text"])


def _anthropic_request(root: str, key: str, model: str, query: str):
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
            "messages": [{"role": "user", "content": query}],
            "tools": [
                {
                    "name": "rewrite_query",
                    "description": "다시 쓴 검색어를 보고한다.",
                    "input_schema": _SCHEMA,
                }
            ],
            # 판정 쪽과 같은 이유 — 강제하지 않으면 산문 응답이 스키마 위반이 된다.
            "tool_choice": {"type": "tool", "name": "rewrite_query"},
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


class RewriteClient:
    def __init__(
        self,
        gms_base_url: str,
        api_key: str,
        chain: Sequence[tuple[str, str]],
        *,
        timeout: float,
        retry: RetryPolicy,
        cache_size: int = 256,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._root = gms_base_url.split("/gmsapi/")[0] + "/gmsapi"
        self._key = api_key
        # resolve_chain 으로 벤더 이름을 검증하고, 요청 생성은 이 파일의 빌더를 쓴다.
        self._chain = [(c.adapter.vendor, c.model) for c in resolve_chain(chain)]
        self._timeout = timeout
        self._retry = retry
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = cache_size
        self._transport = transport

    def _for(self, attempt: int) -> tuple[str, str]:
        return self._chain[min(attempt, len(self._chain) - 1)]

    async def rewrite(self, query: str) -> str:
        """질의를 다듬은 한 줄을 돌려준다. 실패는 오류로 던진다 — 강등은 호출자 몫이다.

        빈 결과·공백뿐인 결과는 스키마 위반으로 취급한다. 재작성이 원문보다 훨씬 길면
        (4배 초과) 모델이 개념을 덧붙인 것이므로 버리고 원문을 돌려준다 — 「뜻을 더하지
        않는다」 규칙의 기계 방어다.
        """
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        attempts = itertools.count()
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:

            async def attempt() -> str:
                return await self._once(client, self._for(next(attempts)), query)

            try:
                result = await call_with_retry(attempt, self._retry, stage="rewrite")
            except SchemaViolationError as exc:
                raise PermanentError(
                    f"rewrite schema violation after {self._retry.attempts} attempt(s): {exc}"
                ) from exc

        rewritten = result.strip()
        if not rewritten or len(rewritten) > max(len(query) * 4, 40):
            rewritten = query
        self._cache[query] = rewritten
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return rewritten

    async def _once(
        self, client: httpx.AsyncClient, call: tuple[str, str], query: str
    ) -> str:
        vendor, model = call
        build, parse = _BUILDERS[vendor]
        url, headers, body = build(self._root, self._key, model, query)
        async with meter.call("rewrite", model=model, vendor=vendor) as rec:
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                rec.status = type(exc).__name__
                raise TransientError(
                    f"rewrite request failed ({vendor}:{model}): {redact(str(exc))}"
                ) from exc

            rec.status = resp.status_code
            if resp.status_code != 200:
                raise classify_http_status(
                    resp.status_code,
                    f"rewrite error: {vendor}:{model} {resp.status_code} "
                    f"{redact_body(resp.text)}",
                )

            try:
                payload = resp.json()
                record_usage("rewrite", payload, vendor=vendor, model=model)
                data = parse(payload)
                return str(data["query"])
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise SchemaViolationError(
                    f"rewrite parse failed ({vendor}:{model}): {exc}"
                ) from exc

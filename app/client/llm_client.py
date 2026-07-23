"""LLM 판정 클라이언트 (GMS 게이트웨이, Gemini generateContent).

테스트 C-2에서 확정한 호출 방식을 그대로 옮겼다(tools/keyword_eval/test_c_judge.py):
gemini-2.5-flash + responseSchema(네이티브 구조화 출력) + thinkingBudget=0.
function-calling은 2.5-flash에서 코드형 호출로 malformed 되므로 쓰지 않는다.

GMS는 도메인별 네이티브 인증을 통과시킨다 — Gemini는 x-goog-api-key.
client는 DB를 모른다. HTTP 실패는 분류된 오류로 service까지 올린다.
"""
from __future__ import annotations

import json

import httpx

from app.core.errors import TransientError
from app.schema.llm import JudgeResult, KeywordSelection

_TIMEOUT = 90.0

# 테스트 C-1에서 확정한 프롬프트. 부대시설/서비스 제외 규칙 포함
# (prompts/keyword_judgment.md).
SYSTEM = (
    "당신은 장소 기록 서비스의 Keyword 분류기입니다.\n"
    "사용자가 장소를 저장한 이유를 적은 짧은 글(Context)과 후보 Keyword 목록이 주어집니다.\n"
    "후보 목록에서 이 Context에 실제로 들어맞는 Keyword만 고르세요.\n"
    "규칙:\n"
    "- 반드시 후보 목록의 keyword_id 중에서만 고릅니다. 목록에 없는 것을 만들지 마세요.\n"
    "- 글에서 근거를 찾을 수 있는 것만 고릅니다. 그럴듯하다는 이유로 넣지 마세요.\n"
    "- 하나도 맞지 않으면 빈 목록을 반환합니다. 억지로 채우지 마세요.\n"
    "- 보통 0~3개입니다. 많이 고를수록 정확도가 떨어집니다.\n"
    "- description은 의미 범위, examples는 실제 말투 예시입니다. 둘 다 참고하세요.\n"
    "- 주차·화장실·직원 응대·가격 같은 부대시설이나 서비스 이야기는 장소의 Keyword가 아닙니다. "
    "장소에서 무엇을 했는지·누구와·어떤 분위기였는지만 고르세요.\n"
    "- confidence는 근거의 확실함을 0~1로 나타냅니다. 애매하면 낮게 줍니다.\n"
    "- unmatchedConcepts에는 후보로 표현하지 못한 핵심 개념을 짧게 적습니다(없으면 빈 배열)."
)

# keywordId enum은 두지 않는다. 후보 밖 값은 매핑 단계에서 폐기한다(keyword-preset.md §4.3).
_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["selected"],
    "properties": {
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["keywordId", "confidence"],
                "properties": {
                    "keywordId": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "unmatchedConcepts": {"type": "array", "items": {"type": "string"}},
    },
}


def build_user(context_text: str, candidates: list[dict]) -> str:
    lines = []
    for p in candidates:
        examples = " · ".join(p.get("examples", []))
        lines.append(
            f"- id={p['id']} | {p['display_name']} ({p['category']}) | "
            f"의미: {p['description']} | 예: {examples}"
        )
    return f"[Context]\n{context_text}\n\n[후보 Keyword]\n" + "\n".join(lines)


class LLMClient:
    def __init__(self, gms_base_url: str, api_key: str, model: str) -> None:
        # GMS root에서 Gemini 네이티브 경로를 파생한다.
        self._root = gms_base_url.split("/gmsapi/")[0] + "/gmsapi"
        self._key = api_key
        self._model = model

    async def judge(self, context_text: str, candidates: list[dict]) -> JudgeResult:
        user = build_user(context_text, candidates)
        url = (
            f"{self._root}/generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent"
        )
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _RESPONSE_SCHEMA,
                "maxOutputTokens": 2048,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    headers={
                        "x-goog-api-key": self._key,
                        "content-type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise TransientError(f"llm request failed: {exc}") from exc

        if resp.status_code != 200:
            # 5xx·일시 오류는 물론, 게이트웨이 오류도 재스캔으로 회수되게 일시 오류로 둔다.
            raise TransientError(
                f"llm error: {resp.status_code} {resp.text[:200]}"
            )

        return self._parse(resp.json())

    @staticmethod
    def _parse(payload: dict) -> JudgeResult:
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise TransientError(f"llm parse failed: {exc}") from exc

        selected = [
            KeywordSelection(
                keyword_id=int(s["keywordId"]),
                confidence=(
                    float(s["confidence"]) if s.get("confidence") is not None else None
                ),
            )
            for s in data.get("selected", [])
            if "keywordId" in s
        ]
        unmatched = [str(x) for x in data.get("unmatchedConcepts", [])]
        return JudgeResult(selected=selected, unmatched_concepts=unmatched)

"""GMS Gemini 이미지 입력으로 장소명·근거·맥락 후보를 추출한다."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from typing import Any, Protocol

import httpx
from PIL import Image
from pydantic import ValidationError

from app.client._calls import meter
from app.core.image_validation import ValidatedImage
from app.core.place_suggestion import (
    ImageProcessingError,
    VisionPermanentError,
    VisionTransientError,
    classify_vision_status,
)
from app.schema.place_suggestion import ExtractedPlace, PlaceExtractionResult
from app.schema.vision_analysis import VisionAnalysisResult

DEVELOPER_PROMPT = (
    "한국어로 답변하세요. 이미지에 실제로 보이는 정보만 추출하고, "
    "반드시 지정된 JSON 스키마를 준수하세요. 특히 context_suggestion은 "
    "반드시 '장소의 특징. 메뉴 또는 방문 의도' 순서의 짧은 메모체로 작성하세요. "
    "예: '수제 버거가 맛있는 곳. 통새우버거 먹고 싶음'. "
    "일반적인 서술형 문장이나 메뉴 나열만 반환하면 안 됩니다."
)

USER_PROMPT = """
카카오톡 대화 캡처 이미지를 분석해 PinLog에 기록할 장소와 맥락을 추출하세요.

반드시 이미지에 실제로 보이는 정보만 사용하세요.

작업 규칙:
1. 이미지에 보이는 텍스트를 읽기 순서대로 full_text에 가능한 그대로 전사합니다.
   요약하거나 맞춤법을 고치지 마세요.
2. 장소명 또는 장소 공유 카드에서 장소 후보를 최대 3개 추출합니다.
3. 각 장소 후보 안에 다음 정보를 서로 연결해서 반환합니다.
   - place_name: 대화에서 확인한 장소명
   - area_hint: 지역, 동네, 지점 단서. 없으면 null
   - evidence: 해당 장소를 판단한 원문 근거. 없으면 null
   - visit_status
     * WANT_TO_VISIT: 추천받음, 방문 제안, 앞으로 가려는 표현
     * VISITED: 실제 방문 경험을 과거형으로 언급
     * UNKNOWN: 판단할 근거가 부족함
   - context_suggestion: PinLog에 저장할 짧은 메모. 아래 6번 형식을 반드시 따름
   - context_evidence: 맥락을 판단한 원문 근거
   - confidence: 해당 장소 분석 신뢰도 0~1
4. 장소가 여러 개면 각 장소와 해당 장소의 맥락을 섞지 마세요.
5. 추천받은 장소는 WANT_TO_VISIT으로 판단합니다.
6. context_suggestion 작성 형식:
   - 대화에 근거가 있으면 "장소의 특징. 메뉴 또는 방문 의도" 순서의 짧은 두 문장으로 작성하세요.
   - 첫 문장은 "~인 곳.", "~가 맛있는 곳.", "~하기 좋은 곳."처럼 장소 특징을 요약하세요.
   - 둘째 문장은 "~ 먹고 싶음", "~ 가보고 싶음", "~ 다시 가고 싶음"처럼 간결한 메모체로 작성하세요.
   - 같은 내용을 반복하거나 장소명을 문장에 다시 넣지 마세요.
   - 특징이나 의도 중 하나만 근거가 있으면 근거가 있는 한 문장만 작성하세요.
   - 예시: "수제 버거가 맛있는 곳. 통새우버거 먹고 싶음"
   - 예시: "분위기가 조용한 카페. 공부하러 가보고 싶음"
   - 방문 경험 예시: "가지튀김이 맛있었던 곳. 다시 가고 싶음"
7. 이미지에 없는 메뉴, 맛, 분위기, 평가, 동행자, 시기 등을 추측하지 마세요.
8. 장소 근거가 부족하면 장소 후보를 만들지 마세요.
9. 맥락 근거가 부족하면 context_suggestion과 context_evidence를 null로 반환하세요.
10. confidence는 전체 결과에 대한 신뢰도를 0~1 사이로 반환합니다.
11. 출력은 지정된 JSON 스키마만 따르고 설명 문장을 추가하지 마세요.
""".strip()

MODEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "full_text": {"type": "string"},
        "place_candidates": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "place_name": {"type": "string", "minLength": 1},
                    "area_hint": {
                        "anyOf": [{"type": "string"}, {"type": "null"}]
                    },
                    "evidence": {
                        "anyOf": [{"type": "string"}, {"type": "null"}]
                    },
                    "visit_status": {
                        "type": "string",
                        "enum": ["WANT_TO_VISIT", "VISITED", "UNKNOWN"],
                    },
                    "context_suggestion": {
                        "anyOf": [
                            {
                                "type": "string",
                                "description": (
                                    "'장소의 특징. 메뉴 또는 방문 의도' 순서의 "
                                    "짧은 메모체"
                                ),
                            },
                            {"type": "null"},
                        ]
                    },
                    "context_evidence": {
                        "anyOf": [{"type": "string"}, {"type": "null"}]
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "place_name",
                    "area_hint",
                    "evidence",
                    "visit_status",
                    "context_suggestion",
                    "context_evidence",
                    "confidence",
                ],
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["full_text", "place_candidates", "confidence"],
}


class VisionClient(Protocol):
    async def extract(self, image: ValidatedImage) -> PlaceExtractionResult: ...


class GmsGeminiVisionClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        gms_base_url: str,
        api_key: str,
        model: str,
        timeout_sec: float,
        max_image_bytes: int,
        max_request_bytes: int,
    ) -> None:
        root = gms_base_url.split("/gmsapi/")[0] + "/gmsapi"
        self._url = (
            f"{root}/generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        self._http = http
        self._key = api_key
        self._model = model
        self._timeout = timeout_sec
        self._max_image_bytes = max_image_bytes
        self._max_request_bytes = max_request_bytes

    async def extract(self, image: ValidatedImage) -> PlaceExtractionResult:
        try:
            media_type, compact = await asyncio.to_thread(
                compact_image, image.content, self._max_image_bytes
            )
        except ImageProcessingError:
            raise
        except (OSError, ValueError) as exc:
            raise ImageProcessingError("image compression failed") from exc
        payload = self._payload(media_type, compact)
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(body) > self._max_request_bytes:
            raise ImageProcessingError("vision request exceeds the local safe limit")

        async with meter.call("vision", model=self._model, vendor="gemini") as rec:
            try:
                response = await self._http.post(
                    self._url,
                    headers={
                        "x-goog-api-key": self._key,
                        "content-type": "application/json",
                    },
                    content=body,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                rec.status = type(exc).__name__
                raise VisionTransientError(
                    f"vision request failed: {type(exc).__name__}"
                ) from exc

            rec.status = response.status_code
            if response.status_code != 200:
                # GMS는 큰 본문도 모델 오류처럼 답한다. 문자열로 크기 오류를 추론하지 않는다.
                raise classify_vision_status(response.status_code)

            try:
                output = _extract_gemini_text(response.json())
                analysis = VisionAnalysisResult.model_validate_json(
                    _strip_fence(output)
                )
                return _to_place_extraction(analysis)
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise VisionPermanentError("vision response schema invalid") from exc

    @staticmethod
    def _payload(media_type: str, content: bytes) -> dict[str, Any]:
        return {
            "contents": [
                {
                    "parts": [
                        {"text": f"{DEVELOPER_PROMPT}\n\n{USER_PROMPT}"},
                        {
                            "inline_data": {
                                "mime_type": media_type,
                                "data": base64.b64encode(content).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": MODEL_SCHEMA,
                "thinkingConfig": {"thinkingLevel": "LOW"},
            },
        }


def _to_place_extraction(analysis: VisionAnalysisResult) -> PlaceExtractionResult:
    candidates = []
    for candidate in analysis.place_candidates:
        candidates.append(
            ExtractedPlace(
                place_name=candidate.place_name,
                region_hints=[candidate.area_hint] if candidate.area_hint else [],
                branch_hint=None,
                evidence=_evidence_lines(
                    candidate.evidence,
                    candidate.context_evidence,
                ),
                context_suggestion=candidate.context_suggestion,
            )
        )
    return PlaceExtractionResult(candidates=candidates)


def _evidence_lines(*values: str | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        for line in value.splitlines():
            normalized = line.strip()
            key = normalized.casefold()
            if normalized and key not in seen:
                result.append(normalized)
                seen.add(key)
            if len(result) == 5:
                return result
    return result


def compact_image(content: bytes, max_bytes: int) -> tuple[str, bytes]:
    """본문 상한을 넘지 않도록 JPEG 품질과 치수를 단계적으로 낮춘다."""
    with Image.open(io.BytesIO(content)) as source:
        image = source.convert("RGB")
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        while True:
            for quality in (80, 70, 60, 50, 40, 30):
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                compact = buffer.getvalue()
                if len(compact) <= max_bytes:
                    return "image/jpeg", compact

            if max(image.size) <= 480:
                raise ImageProcessingError("image cannot fit the GMS safe limit")
            image.thumbnail(
                (
                    max(480, int(image.width * 0.8)),
                    max(480, int(image.height * 0.8)),
                ),
                Image.Resampling.LANCZOS,
            )


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    text = "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ).strip()
    if not text:
        raise ValueError("vision response has no text")
    return text


def _strip_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value

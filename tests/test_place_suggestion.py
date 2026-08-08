from __future__ import annotations

import asyncio
import io
import json
import logging
import os

import httpx
import pytest
from fastapi import UploadFile
from PIL import Image
from starlette.datastructures import Headers

from app.client.vision_client import USER_PROMPT, GmsGeminiVisionClient, compact_image
from app.core.errors import PermanentError, TransientError
from app.core.image_validation import ValidatedImage, validate_image
from app.core.place_suggestion import ImageInputError
from app.main import create_app
from app.schema.place_suggestion import (
    ExtractedPlace,
    KakaoPlace,
    PlaceExtractionResult,
    PlaceSuggestionResponse,
)
from app.service.place_suggestion_service import PlaceSuggestionService, build_kakao_query

HDR = {"X-Internal-Secret": "test-secret", "X-Trace-Id": "trace-place-1"}


def _image_bytes(*, size: tuple[int, int] = (800, 1200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(content: bytes | None = None) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content or _image_bytes()),
        filename="chat.png",
        headers=Headers({"content-type": "image/png"}),
    )


def _incompressible_image_bytes(*, size: tuple[int, int]) -> bytes:
    """랜덤 픽셀은 PNG로도 거의 압축되지 않아, 파일 크기를 해상도로 예측 가능하게 만든다."""
    random_pixels = os.urandom(size[0] * size[1] * 3)
    buffer = io.BytesIO()
    Image.frombytes("RGB", size, random_pixels).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeVision:
    def __init__(self, result: PlaceExtractionResult, blocker: asyncio.Event | None = None):
        self.result = result
        self.blocker = blocker

    async def extract(self, _image):
        if self.blocker is not None:
            await self.blocker.wait()
        return self.result


class FakeKakao:
    async def search(self, _query: str, _limit: int = 3) -> list[KakaoPlace]:
        return [
            KakaoPlace(
                kakao_place_id="123",
                name="주토피아 서울",
                address="서울 중구 테스트로 1",
                lat=37.5,
                lng=127.0,
            )
        ]


def _service(*, blocker: asyncio.Event | None = None) -> PlaceSuggestionService:
    result = PlaceExtractionResult(
        candidates=[
            ExtractedPlace(
                place_name="주토피아 서울",
                region_hints=["서울"],
                evidence=["대구랑 서울에만 있어"],
                context_suggestion="대구랑 서울에만 있다는 화덕피자집",
            )
        ]
    )
    return PlaceSuggestionService(
        FakeVision(result, blocker),
        FakeKakao(),
        max_upload_bytes=5 * 1024 * 1024,
        max_concurrency=1,
        timeout_sec=30,
    )


async def test_internal_endpoint_requires_shared_secret():
    app = create_app()
    app.state.place_suggestion_service = _service()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/v1/place-suggestions",
            files={"image": ("chat.png", _image_bytes(), "image/png")},
        )
    assert response.status_code == 401


async def test_internal_endpoint_returns_raw_spring_contract():
    app = create_app()
    app.state.place_suggestion_service = _service()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/v1/place-suggestions",
            headers=HDR,
            files={"image": ("chat.png", _image_bytes(), "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["requestId"] == "trace-place-1"
    assert "success" not in body and "data" not in body
    assert body["candidates"][0]["extracted"]["placeName"] == "주토피아 서울"
    assert len(body["candidates"][0]["kakaoSearch"]["items"]) == 1


async def test_internal_endpoint_rejects_missing_image_as_contract_400():
    app = create_app()
    app.state.place_suggestion_service = _service()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/v1/place-suggestions", headers=HDR
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "INVALID_IMAGE_COUNT"


async def test_internal_endpoint_returns_named_busy_error():
    class BusyService:
        async def suggest(self, _upload, *, request_id=None):
            return None

    app = create_app()
    app.state.place_suggestion_service = BusyService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/v1/place-suggestions",
            headers=HDR,
            files={"image": ("chat.png", _image_bytes(), "image/png")},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == "PLACE_SUGGESTION_BUSY"


async def test_second_vision_request_is_rejected_without_queueing():
    blocker = asyncio.Event()
    service = _service(blocker=blocker)
    first = asyncio.create_task(service.suggest(_upload()))
    await asyncio.sleep(0)

    second = await service.suggest(_upload())
    assert second is None

    blocker.set()
    result = await first
    assert isinstance(result, PlaceSuggestionResponse)


async def test_development_result_log_contains_place_and_context(caplog):
    service = _service()
    service._log_results = True
    caplog.set_level(logging.INFO, logger="app.service.place_suggestion")

    await service.suggest(_upload(), request_id="trace-result-log")

    message = caplog.records[-1].getMessage()
    assert "request_id=trace-result-log" in message
    assert "place_name='주토피아 서울'" in message
    assert "context='대구랑 서울에만 있다는 화덕피자집'" in message


async def test_validate_image_rejects_content_over_max_bytes():
    max_bytes = 10 * 1024 * 1024
    oversized = _upload(b"\xff" * (max_bytes + 1))

    with pytest.raises(ImageInputError) as excinfo:
        await validate_image(oversized, max_bytes=max_bytes)

    assert excinfo.value.status_code == 413
    assert excinfo.value.code == "IMAGE_TOO_LARGE"


async def test_validate_image_accepts_a_real_image_within_the_new_ten_mib_limit():
    # S15P11A705-366: 5MiB -> 10MiB. 5MiB보다 크고 10MiB보다 작은 실제 이미지는 예전 상한에서는
    # 거부됐지만(RED) 새 상한에서는 통과해야 한다(GREEN).
    content = _incompressible_image_bytes(size=(1600, 1536))
    assert 5 * 1024 * 1024 < len(content) < 10 * 1024 * 1024

    result = await validate_image(_upload(content), max_bytes=10 * 1024 * 1024)

    assert result.media_type == "image/png"
    assert (result.width, result.height) == (1600, 1536)


def test_kakao_query_does_not_add_conflicting_region_to_named_branch():
    candidate = ExtractedPlace(
        place_name="주토피아 서울",
        region_hints=["서울", "대구"],
        evidence=["주토피아 서울 가자"],
        context_suggestion="대구랑 서울에만 있다는 화덕피자집",
    )

    assert build_kakao_query(candidate) == "주토피아 서울"


def test_compaction_keeps_gms_image_under_measured_safe_target():
    media_type, compact = compact_image(_image_bytes(size=(1440, 2560)), 50_000)
    assert media_type == "image/jpeg"
    assert len(compact) <= 50_000


def test_vision_payload_uses_low_thinking_level():
    payload = GmsGeminiVisionClient._payload("image/jpeg", b"image")

    thinking = payload["generationConfig"]["thinkingConfig"]
    assert thinking == {"thinkingLevel": "LOW"}
    assert "thinkingBudget" not in thinking
    assert "systemInstruction" not in payload
    assert "full_text" in payload["contents"][0]["parts"][0]["text"]
    assert payload["generationConfig"]["responseSchema"]["required"] == [
        "full_text",
        "place_candidates",
        "confidence",
    ]
    assert "maxOutputTokens" not in payload["generationConfig"]


def test_context_prompt_restores_benchmark_writing_rules():
    assert "full_text에 가능한 그대로 전사" in USER_PROMPT
    assert "visit_status" in USER_PROMPT
    assert "context_evidence" in USER_PROMPT
    assert '"장소의 특징. 메뉴 또는 방문 의도"' in USER_PROMPT
    assert '"~ 다시 가고 싶음"' in USER_PROMPT
    assert "장소명을 문장에 다시 넣지 마세요" in USER_PROMPT


async def test_benchmark_analysis_maps_context_evidence_to_public_contract():
    model_output = json.dumps(
        {
            "full_text": "코즈믹버거\n와사비 통새우버거 다 맛있어보여",
            "place_candidates": [
                {
                    "place_name": "코즈믹버거",
                    "area_hint": "강남구청역",
                    "evidence": "코즈믹버거?",
                    "visit_status": "WANT_TO_VISIT",
                    "context_suggestion": (
                        "수제 버거가 맛있는 곳. 와사비 통새우버거 먹고 싶음"
                    ),
                    "context_evidence": (
                        "수제 버거 먹으러가려규!!\n와사비 통새우버거 다 맛있어보여"
                    ),
                    "confidence": 0.95,
                }
            ],
            "confidence": 0.95,
        },
        ensure_ascii=False,
    )
    response_body = {
        "candidates": [{"content": {"parts": [{"text": model_output}]}}]
    }
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=response_body)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = GmsGeminiVisionClient(
            http,
            gms_base_url="https://gms.example/gmsapi/api.openai.com/v1",
            api_key="secret",
            model="gemini-3.5-flash",
            timeout_sec=20,
            max_image_bytes=50_000,
            max_request_bytes=90_000,
        )
        result = await client.extract(
            ValidatedImage(_image_bytes(), "image/png", 800, 1200)
        )

    candidate = result.candidates[0]
    assert candidate.place_name == "코즈믹버거"
    assert candidate.region_hints == ["강남구청역"]
    assert candidate.context_suggestion.endswith("와사비 통새우버거 먹고 싶음")
    assert "와사비 통새우버거 다 맛있어보여" in candidate.evidence


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(429, TransientError), (503, TransientError), (400, PermanentError)],
)
async def test_vision_status_uses_existing_error_taxonomy(status, error_type):
    transport = httpx.MockTransport(lambda _request: httpx.Response(status))
    async with httpx.AsyncClient(transport=transport) as http:
        client = GmsGeminiVisionClient(
            http,
            gms_base_url="https://gms.example/gmsapi/api.openai.com/v1",
            api_key="secret",
            model="gemini-3.5-flash",
            timeout_sec=20,
            max_image_bytes=50_000,
            max_request_bytes=90_000,
        )
        image = ValidatedImage(_image_bytes(), "image/png", 800, 1200)
        with pytest.raises(error_type):
            await client.extract(image)

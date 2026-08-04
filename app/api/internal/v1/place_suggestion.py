"""POST /internal/v1/place-suggestions — Spring 전용 동기 장소 제안."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.core.place_suggestion import ImageInputError, ImageProcessingError
from app.schema.place_suggestion import PlaceSuggestionResponse
from app.service.place_suggestion_service import PlaceSuggestionService

router = APIRouter()


def get_service(request: Request) -> PlaceSuggestionService:
    service = getattr(request.app.state, "place_suggestion_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="PLACE_SUGGESTION_UNAVAILABLE")
    return service


@router.post("/place-suggestions", response_model=PlaceSuggestionResponse)
async def suggest_place(
    request: Request,
    images: Annotated[list[UploadFile] | None, File(alias="image")] = None,
) -> PlaceSuggestionResponse:
    uploads = images or []
    if len(uploads) != 1:
        raise HTTPException(status_code=400, detail="INVALID_IMAGE_COUNT")

    upload = uploads[0]
    request_id = _request_id(request)
    try:
        try:
            response = await get_service(request).suggest(upload, request_id=request_id)
        except ImageInputError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
        except ImageProcessingError as exc:
            raise HTTPException(
                status_code=422, detail="IMAGE_PROCESSING_FAILED"
            ) from exc

        if response is None:
            raise HTTPException(status_code=503, detail="PLACE_SUGGESTION_BUSY")
        return response
    finally:
        await upload.close()


def _request_id(request: Request) -> str:
    value = request.headers.get("X-Trace-Id") or request.headers.get("X-Request-Id")
    if value:
        value = value.strip()
        if value and len(value) <= 128:
            return value
    return f"req_{uuid.uuid4().hex}"

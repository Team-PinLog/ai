"""업로드 크기를 제한하고 실제 이미지 형식·해상도를 검증한다."""
from __future__ import annotations

import asyncio
import io
import warnings
from dataclasses import dataclass

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.place_suggestion import ImageInputError

READ_CHUNK_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg"}
FORMAT_TO_MEDIA_TYPE = {"PNG": "image/png", "JPEG": "image/jpeg"}


@dataclass(frozen=True)
class ValidatedImage:
    content: bytes
    media_type: str
    width: int
    height: int


async def validate_image(upload: UploadFile, *, max_bytes: int) -> ValidatedImage:
    if upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise ImageInputError(415, "UNSUPPORTED_IMAGE_TYPE")

    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(READ_CHUNK_BYTES):
        size += len(chunk)
        if size > max_bytes:
            raise ImageInputError(413, "IMAGE_TOO_LARGE")
        chunks.append(chunk)

    content = b"".join(chunks)
    if not content:
        raise ImageInputError(400, "INVALID_IMAGE")

    try:
        media_type, width, height = await asyncio.to_thread(_inspect_image, content)
    except ImageInputError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ImageInputError(400, "INVALID_IMAGE") from exc
    return ValidatedImage(content, media_type, width, height)


def _inspect_image(content: bytes) -> tuple[str, int, int]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ImageInputError(400, "INVALID_IMAGE")
            image.verify()

    media_type = FORMAT_TO_MEDIA_TYPE.get(image_format)
    if media_type is None:
        raise ImageInputError(415, "UNSUPPORTED_IMAGE_TYPE")
    return media_type, width, height

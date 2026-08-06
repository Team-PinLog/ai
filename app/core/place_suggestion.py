"""이미지 장소 제안에만 쓰는 입력·부분 실패 오류."""
from __future__ import annotations

from app.core.errors import PermanentError, TransientError


class ImageInputError(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(code)


class ImageProcessingError(Exception):
    """유효한 업로드를 GMS 안전 본문으로 만들 수 없을 때."""


class KakaoSearchError(Exception):
    """후보 하나의 카카오 검색 실패. 전체 요청 실패로 올리지 않는다."""


class VisionTransientError(TransientError):
    """GMS 비전 경로의 429·5xx·네트워크 실패."""


class VisionPermanentError(PermanentError):
    """GMS 비전 경로의 설정·요청·응답 형식 실패."""


def classify_vision_status(status_code: int) -> TransientError | PermanentError:
    if status_code == 429 or status_code >= 500:
        return VisionTransientError(f"vision upstream status={status_code}")
    return VisionPermanentError(f"vision upstream status={status_code}")

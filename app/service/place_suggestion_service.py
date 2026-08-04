"""이미지 추출 뒤 후보별 카카오 검색을 조립한다."""
from __future__ import annotations

import asyncio
import math
import re
import unicodedata
import uuid

from fastapi import UploadFile

from app.client.kakao_local_client import KakaoLocalClient
from app.client.vision_client import VisionClient
from app.core.image_validation import validate_image
from app.core.logging import get_logger
from app.core.place_suggestion import KakaoSearchError, VisionTransientError
from app.schema.place_suggestion import (
    ExtractedPlace,
    KakaoPlace,
    KakaoSearchResult,
    KakaoSearchStatus,
    PlaceSuggestionCandidate,
    PlaceSuggestionResponse,
    SuggestionWarning,
    SuggestionWarningCode,
)

MAX_PLACE_CANDIDATES = 3
MAX_KAKAO_CANDIDATES = 3

log = get_logger("app.service.place_suggestion")


class VisionCapacity:
    """대기열 없이 인스턴스 내 비전 동시 실행 수를 제한한다."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._lock = asyncio.Lock()

    async def try_enter(self) -> bool:
        async with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
            return True

    async def leave(self) -> None:
        async with self._lock:
            self._active -= 1


class PlaceSuggestionService:
    def __init__(
        self,
        vision: VisionClient,
        kakao: KakaoLocalClient,
        *,
        max_upload_bytes: int,
        max_concurrency: int = 1,
        timeout_sec: float = 30.0,
        log_results: bool = False,
    ) -> None:
        self._vision = vision
        self._kakao = kakao
        self._max_upload_bytes = max_upload_bytes
        self._capacity = VisionCapacity(max_concurrency)
        self._timeout = timeout_sec
        self._log_results = log_results

    async def suggest(
        self, upload: UploadFile, *, request_id: str | None = None
    ) -> PlaceSuggestionResponse | None:
        """None은 대기 없는 동시성 제한에 걸렸다는 뜻이다."""
        if not await self._capacity.try_enter():
            return None

        try:
            async with asyncio.timeout(self._timeout):
                return await self._suggest(upload, request_id=request_id)
        except TimeoutError as exc:
            raise VisionTransientError("place suggestion deadline exceeded") from exc
        finally:
            await self._capacity.leave()

    async def _suggest(
        self, upload: UploadFile, *, request_id: str | None
    ) -> PlaceSuggestionResponse:
        """동시성 슬롯을 확보한 요청의 실제 처리."""
        image = await validate_image(upload, max_bytes=self._max_upload_bytes)
        extraction = await self._vision.extract(image)
        # evidence가 없는 모델 후보는 규칙 위반이다. 전체 요청을 깨지 않고 폐기한다.
        extracted = [
            candidate
            for candidate in extraction.candidates[:MAX_PLACE_CANDIDATES]
            if candidate.evidence
        ]
        response_id = request_id or f"req_{uuid.uuid4().hex}"
        self._log_extraction(response_id, extracted)
        if not extracted:
            return PlaceSuggestionResponse(
                request_id=response_id,
                warnings=[
                    SuggestionWarning(
                        code=SuggestionWarningCode.NO_PLACE_CANDIDATES,
                        message="대화에서 장소 후보를 찾지 못했습니다.",
                    )
                ],
            )

        results = await asyncio.gather(
            *(
                self._search_candidate(index, candidate)
                for index, candidate in enumerate(extracted, start=1)
            )
        )
        return PlaceSuggestionResponse(
            request_id=response_id,
            candidates=[candidate for candidate, _ in results],
            warnings=[warning for _, warnings in results for warning in warnings],
        )

    def _log_extraction(
        self, request_id: str, candidates: list[ExtractedPlace]
    ) -> None:
        if not self._log_results:
            return
        if not candidates:
            log.info("place suggestion result request_id=%s candidates=0", request_id)
            return
        for index, candidate in enumerate(candidates, start=1):
            log.info(
                "place suggestion result request_id=%s candidate=%d place_name=%r context=%r",
                request_id,
                index,
                candidate.place_name,
                candidate.context_suggestion,
            )

    async def _search_candidate(
        self, index: int, extracted: ExtractedPlace
    ) -> tuple[PlaceSuggestionCandidate, list[SuggestionWarning]]:
        candidate_id = f"candidate-{index}"
        query = build_kakao_query(extracted)
        warnings: list[SuggestionWarning] = []
        try:
            places = await self._kakao.search(query, MAX_KAKAO_CANDIDATES)
        except KakaoSearchError:
            status = KakaoSearchStatus.FAILED
            places = []
            warnings.append(
                SuggestionWarning(
                    code=SuggestionWarningCode.KAKAO_SEARCH_PARTIAL_FAILURE,
                    message="일부 장소의 카카오 검색에 실패했습니다.",
                    candidate_id=candidate_id,
                )
            )
        else:
            recordable = [place for place in places if is_recordable(place)]
            if len(recordable) != len(places):
                warnings.append(
                    SuggestionWarning(
                        code=SuggestionWarningCode.KAKAO_PLACE_NOT_RECORDABLE,
                        message="저장할 수 없는 장소 후보를 제외했습니다.",
                        candidate_id=candidate_id,
                    )
                )
            places = recordable
            status = (
                KakaoSearchStatus.SUCCESS
                if places
                else KakaoSearchStatus.NO_RESULTS
            )

        return (
            PlaceSuggestionCandidate(
                candidate_id=candidate_id,
                extracted=extracted,
                kakao_search=KakaoSearchResult(
                    status=status,
                    query=query,
                    items=places,
                ),
            ),
            warnings,
        )


def is_recordable(place: KakaoPlace) -> bool:
    return (
        0 < len(place.kakao_place_id) <= 50
        and 0 < len(place.name) <= 100
        and 0 < len(place.address) <= 200
        and (place.road_address is None or len(place.road_address) <= 200)
        and (place.phone is None or len(place.phone) <= 30)
        and (place.place_url is None or len(place.place_url) <= 300)
        and math.isfinite(place.lat)
        and math.isfinite(place.lng)
        and -90 <= place.lat <= 90
        and -180 <= place.lng <= 180
    )


def build_kakao_query(candidate: ExtractedPlace) -> str:
    parts = [candidate.place_name]
    normalized_name = _normalize(candidate.place_name)
    if candidate.branch_hint and _normalize(candidate.branch_hint) not in normalized_name:
        parts.append(candidate.branch_hint)
    elif not any(_normalize(hint) in normalized_name for hint in candidate.region_hints):
        first_region = next((hint for hint in candidate.region_hints if hint), None)
        if first_region:
            parts.append(first_region)
    return " ".join(parts)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())

"""카카오 로컬 키워드 검색과 장소 스냅샷 정규화."""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from app.core.place_suggestion import KakaoSearchError
from app.schema.place_suggestion import KakaoPlace

KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


class KakaoLocalClient(Protocol):
    async def search(self, query: str, limit: int = 3) -> list[KakaoPlace]: ...


class HttpKakaoLocalClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        api_key: str,
        timeout_sec: float,
    ) -> None:
        self._http = http
        self._key = api_key
        self._timeout = timeout_sec

    async def search(self, query: str, limit: int = 3) -> list[KakaoPlace]:
        response = await self._request(query, limit)
        try:
            payload = response.json()
            documents = payload.get("documents") or []
            return [_to_place(item) for item in documents[:limit]]
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise KakaoSearchError("Kakao Local returned malformed data") from exc

    async def _request(self, query: str, limit: int) -> httpx.Response:
        for attempt in range(2):
            try:
                response = await self._http.get(
                    KAKAO_KEYWORD_URL,
                    headers={"Authorization": f"KakaoAK {self._key}"},
                    params={"query": query, "size": limit},
                    timeout=self._timeout,
                )
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return response
            except (httpx.TimeoutException, httpx.TransportError):
                pass
            except httpx.HTTPStatusError as exc:
                raise KakaoSearchError("Kakao Local rejected the request") from exc

            if attempt == 0:
                await asyncio.sleep(0.2)
        raise KakaoSearchError("Kakao Local request failed")


def _to_place(item: dict[str, Any]) -> KakaoPlace:
    return KakaoPlace(
        kakao_place_id=str(item["id"]),
        name=str(item["place_name"]),
        category_name=_optional_text(item.get("category_name")),
        address=str(item.get("address_name") or "").strip(),
        road_address=_optional_text(item.get("road_address_name")),
        phone=_optional_text(item.get("phone")),
        place_url=_optional_text(item.get("place_url")),
        lat=float(item["y"]),
        lng=float(item["x"]),
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

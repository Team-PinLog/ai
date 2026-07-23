"""서비스 간 공유 시크릿 검증 미들웨어.

/internal/* 경로는 내부 네트워크 전용이며 공유 시크릿 헤더를 요구한다
(architecture.md §7). User 인증은 판단하지 않는다.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

INTERNAL_SECRET_HEADER = "X-Internal-Secret"


class SharedSecretMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, secret: str) -> None:
        super().__init__(app)
        self._secret = secret

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/internal/"):
            if request.headers.get(INTERNAL_SECRET_HEADER) != self._secret:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "invalid internal secret"},
                )
        return await call_next(request)

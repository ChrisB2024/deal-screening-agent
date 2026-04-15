"""Input validation middleware — enforces body size limits before route handlers.

Runs after auth and rate limiting. Checks Content-Length header as a fast
pre-check, then enforces the real limit during body read in route handlers.
"""

from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.observability.logger import get_logger, request_id_var

from .file_validator import MAX_FILE_SIZE_BYTES
from .types import ReasonCode, REASON_USER_MESSAGE

_logger = get_logger("input_validation")

UPLOAD_PATHS = frozenset({"/api/v1/deals/upload"})
EXEMPT_PATHS_PREFIXES = ("/health", "/ready", "/metrics")

MAX_BODY_BYTES = MAX_FILE_SIZE_BYTES + 1024 * 1024  # file limit + 1 MB overhead for multipart framing


class InputValidationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth_required_prefixes: tuple[str, ...] = ()):
        super().__init__(app)
        self._auth_required_prefixes = auth_required_prefixes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith(EXEMPT_PATHS_PREFIXES):
            return await call_next(request)

        if self._auth_required_prefixes and request.url.path.startswith(self._auth_required_prefixes):
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                request_id = getattr(request.state, "request_id", request_id_var.get("unknown"))
                return JSONResponse(
                    status_code=401,
                    headers={"X-Request-ID": request_id},
                    content={"error": "UNAUTHORIZED", "message": "Missing or invalid Authorization header"},
                )

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = 0

            limit = MAX_BODY_BYTES if request.url.path in UPLOAD_PATHS else 1024 * 1024
            if declared_size > limit:
                request_id = getattr(request.state, "request_id", request_id_var.get("unknown"))
                _logger.info(
                    "input_validation.rejected",
                    reason=ReasonCode.BODY_TOO_LARGE.value,
                    declared_size=declared_size,
                    limit=limit,
                    path=request.url.path,
                )
                return JSONResponse(
                    status_code=413,
                    headers={"X-Request-ID": request_id},
                    content={
                        "error": {
                            "code": ReasonCode.BODY_TOO_LARGE.value,
                            "message": REASON_USER_MESSAGE[ReasonCode.BODY_TOO_LARGE],
                            "request_id": request_id,
                        }
                    },
                )

        return await call_next(request)

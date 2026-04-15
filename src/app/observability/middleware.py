"""FastAPI middleware for request context injection.

Sets request_id, tenant_id, user_id into contextvars so every log line
and audit record within a request carries them automatically.
"""

from __future__ import annotations

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .logger import (
    get_logger,
    request_id_var,
    tenant_id_var,
    trace_id_var,
    user_id_var,
)

_logger = get_logger("http")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        t_id = request.headers.get("X-Tenant-ID", "none")
        u_id = "none"  # TODO: extract from JWT once auth is wired
        tr_id = request.headers.get("traceparent", str(uuid.uuid4()).replace("-", ""))

        req_token = request_id_var.set(req_id)
        tenant_token = tenant_id_var.set(t_id)
        user_token = user_id_var.set(u_id)
        trace_token = trace_id_var.set(tr_id)

        request.state.request_id = req_id
        start = time.monotonic()

        try:
            response: Response = await call_next(request)
            duration_ms = int((time.monotonic() - start) * 1000)

            response.headers["X-Request-ID"] = req_id
            _logger.info(
                "http.response",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
            )
            return response
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            _logger.error(
                "http.error",
                method=request.method,
                path=request.url.path,
                error=str(exc),
                duration_ms=duration_ms,
            )
            raise
        finally:
            request_id_var.reset(req_token)
            tenant_id_var.reset(tenant_token)
            user_id_var.reset(user_token)
            trace_id_var.reset(trace_token)

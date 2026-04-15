"""Rate limiting middleware — runs after auth, before handler dispatch.

Applies layered limits: per-IP (always), per-user and per-tenant
(when authenticated). A request must pass ALL applicable scopes.
Fails open on store errors.
"""

from __future__ import annotations

import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

import jwt as pyjwt

from app.observability.logger import get_logger, request_id_var

from .bucket import CheckResult
from .config import EndpointGroup, LimitsTable, Scope, resolve_endpoint_group
from .ip_parser import get_client_ip
from .store import RateLimitStore, InMemoryStore

_logger = get_logger("rate_limiter")

_store: RateLimitStore = InMemoryStore()
_limits: LimitsTable = LimitsTable.from_defaults()
_trusted_proxies: list = []


def init_rate_limiter(
    store: RateLimitStore | None = None,
    limits: LimitsTable | None = None,
    trusted_proxies: list | None = None,
) -> None:
    """Initialize the rate limiter. Called during app startup."""
    global _store, _limits, _trusted_proxies
    if store is not None:
        _store = store
    if limits is not None:
        _limits = limits
    if trusted_proxies is not None:
        _trusted_proxies = trusted_proxies


def _build_key(scope: Scope, key: str, group: EndpointGroup) -> str:
    return f"rl:{scope.value}:{key}:{group.value}"


def _extract_auth_identity(request: Request) -> tuple[str | None, str | None]:
    """Best-effort JWT decode for rate limiting keys — no DB hit."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None, None
    try:
        from app.auth.tokens import verify_access_token
        claims = verify_access_token(auth_header[7:])
        return claims.get("sub"), claims.get("tnt")
    except (pyjwt.InvalidTokenError, RuntimeError):
        return None, None


def _extract_email_from_body(request: Request) -> str | None:
    return None


async def _check_scope(
    scope: Scope, key: str, group: EndpointGroup
) -> CheckResult | None:
    """Check a single scope. Returns None if no limit configured."""
    config = _limits.get(scope, group)
    if config is None:
        return None
    bucket_key = _build_key(scope, key, group)
    return await _store.check(bucket_key, config)


def _rate_limit_headers(result: CheckResult) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(max(0, int(result.tokens_remaining))),
        "X-RateLimit-Reset": str(int(result.reset_at)),
    }


def _deny_response(result: CheckResult, scope: Scope, request_id: str) -> JSONResponse:
    headers = _rate_limit_headers(result)
    headers["Retry-After"] = str(result.retry_after_seconds)
    return JSONResponse(
        status_code=429,
        headers=headers,
        content={
            "error": {
                "code": "RATE_LIMITED",
                "message": "Too many requests. Please slow down.",
                "details": {
                    "scope": scope.value,
                    "retry_after_seconds": result.retry_after_seconds,
                },
                "request_id": request_id,
            }
        },
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip health/metrics endpoints
        if request.url.path.startswith(("/health", "/ready", "/metrics")):
            return await call_next(request)

        request_id = getattr(request.state, "request_id", request_id_var.get("unknown"))
        group = resolve_endpoint_group(request.url.path)
        client_ip = get_client_ip(request, _trusted_proxies)

        # Collect all applicable checks; track the most restrictive denial
        most_restrictive_denial: tuple[CheckResult, Scope] | None = None
        last_allowed: CheckResult | None = None

        try:
            checks: list[tuple[Scope, str]] = [(Scope.IP, client_ip)]

            user_id, tenant_id = _extract_auth_identity(request)
            if user_id:
                checks.append((Scope.USER, user_id))
            if tenant_id:
                checks.append((Scope.TENANT, tenant_id))

            for scope, key in checks:
                result = await _check_scope(scope, key, group)
                if result is None:
                    continue
                if not result.allowed:
                    if (
                        most_restrictive_denial is None
                        or result.retry_after_seconds > most_restrictive_denial[0].retry_after_seconds
                    ):
                        most_restrictive_denial = (result, scope)
                else:
                    last_allowed = result

        except Exception:
            _logger.warning("rate_limiter.store_error", path=request.url.path)
            # Fail open: allow request through
            response = await call_next(request)
            response.headers["X-RateLimit-Status"] = "degraded"
            return response

        if most_restrictive_denial is not None:
            result, scope = most_restrictive_denial
            _logger.info(
                "rate_limiter.denied",
                scope=scope.value,
                group=group.value,
                client_ip=client_ip,
                retry_after=result.retry_after_seconds,
            )
            return _deny_response(result, scope, request_id)

        response = await call_next(request)

        # Attach rate limit headers from the most relevant allowed result
        if last_allowed is not None:
            for k, v in _rate_limit_headers(last_allowed).items():
                response.headers[k] = v

        return response

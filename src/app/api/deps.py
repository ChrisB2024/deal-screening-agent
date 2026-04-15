"""Shared API dependencies — auth context, request tracing.

All protected routes use require_auth to extract identity from a verified JWT.
Legacy header-based identity (X-Tenant-ID / X-User-ID) has been removed.
"""

from fastapi import Request

from app.auth.middleware import AuthContext, require_auth

__all__ = ["AuthContext", "require_auth", "get_request_id"]


async def get_request_id(request: Request) -> str:
    """Get the request_id set by middleware."""
    return getattr(request.state, "request_id", "unknown")

"""Shared API dependencies — auth, tenant context, request tracing.

For MVP, tenant_id is passed as a header. Production should extract it from
the JWT token after auth middleware validates the token.

Spec security invariant: "No endpoint should accept unauthenticated requests.
All API routes require valid JWT." For MVP we use a placeholder header;
real JWT auth is a Session 6+ concern.
"""

import uuid

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db


async def get_tenant_id(
    x_tenant_id: str = Header(..., description="Tenant UUID (MVP: header, prod: from JWT)"),
) -> uuid.UUID:
    """Extract and validate tenant_id from request header.

    MVP simplification: tenant comes from a header. In production, this would
    be extracted from a validated JWT token's claims.
    """
    try:
        return uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID header. Must be a UUID.")


async def get_user_id(
    x_user_id: str = Header(..., description="User UUID (MVP: header, prod: from JWT)"),
) -> uuid.UUID:
    """Extract and validate user_id from request header."""
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-User-ID header. Must be a UUID.")


async def get_request_id(request: Request) -> str:
    """Get the request_id set by middleware."""
    return getattr(request.state, "request_id", "unknown")

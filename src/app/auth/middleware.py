"""Auth middleware — the require_auth dependency that every protected route uses.

This is the ONLY sanctioned way to extract identity from a request.
No module reads JWT claims directly.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import AuthSession

from .tokens import verify_access_token


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    tenant_id: str
    session_id: str
    roles: list[str]


async def require_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": "UNAUTHORIZED", "message": "Missing or invalid Authorization header"},
        )

    token = auth_header[7:]
    try:
        claims = verify_access_token(token)
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail={"error": "UNAUTHORIZED", "message": "Invalid or expired access token"},
        )

    session_id = claims["sid"]
    result = await db.execute(
        select(AuthSession).where(AuthSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()

    if session is None or session.revoked_at is not None:
        raise HTTPException(
            status_code=401,
            detail={"error": "SESSION_REVOKED", "message": "Session has been revoked"},
        )

    return AuthContext(
        user_id=claims["sub"],
        tenant_id=claims["tnt"],
        session_id=claims["sid"],
        roles=claims.get("roles", []),
    )

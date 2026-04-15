"""Auth API routes — /api/v1/auth/*"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

from . import service
from .middleware import AuthContext, require_auth
from .schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserInfo,
)
from .service import InvalidCredentials, InvalidRefreshToken, SessionRevoked

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    try:
        bundle = await service.login(
            db, email=body.email, password=body.password,
            ip_address=ip, user_agent=ua,
        )
    except InvalidCredentials as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": exc.code, "message": exc.message},
        )

    return TokenResponse(
        access_token=bundle.access_token,
        expires_in=bundle.expires_in,
        refresh_token=bundle.refresh_token,
        refresh_expires_in=bundle.refresh_expires_in,
        user=UserInfo(
            user_id=bundle.user_id,
            tenant_id=bundle.tenant_id,
            email=bundle.email,
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else None
    try:
        bundle = await service.refresh(
            db, refresh_token=body.refresh_token, ip_address=ip,
        )
    except (InvalidRefreshToken, SessionRevoked) as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": exc.code, "message": exc.message},
        )

    return TokenResponse(
        access_token=bundle.access_token,
        expires_in=bundle.expires_in,
        refresh_token=bundle.refresh_token,
        refresh_expires_in=bundle.refresh_expires_in,
        user=UserInfo(
            user_id=bundle.user_id,
            tenant_id=bundle.tenant_id,
            email=bundle.email,
        ),
    )


@router.post("/logout", status_code=204)
async def logout(
    body: LogoutRequest,
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await service.logout(db, refresh_token=body.refresh_token)


@router.get("/me")
async def me(
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    user = await service.get_user_by_id(db, auth.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserInfo(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        email=user.email,
    )

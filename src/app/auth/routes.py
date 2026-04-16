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
    RegisterRequest,
    TokenResponse,
    UserInfo,
)
from .service import InvalidCredentials, InvalidRefreshToken, SessionRevoked

router = APIRouter()

# Default tenant for self-registration (single-tenant portfolio phase)
_DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from app.models.user import User

    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = await service.create_user(
        db, tenant_id=_DEFAULT_TENANT_ID, email=body.email, password=body.password,
    )

    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    bundle = await service.login(
        db, email=body.email, password=body.password,
        ip_address=ip, user_agent=ua,
    )
    await db.commit()

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

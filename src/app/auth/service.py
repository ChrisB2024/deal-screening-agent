"""Auth service — login, refresh, logout, session revocation.

All DB writes (session, token, audit) happen in the caller's transaction
so they commit or roll back atomically (spec invariant #10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AuthAuditLog, AuthSession, RefreshToken, User, _prefixed_id
from app.observability.logger import get_logger

from .passwords import dummy_verify, hash_password, verify_password
from .tokens import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

_logger = get_logger("auth")


# --- Errors ---


class AuthError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class InvalidCredentials(AuthError):
    def __init__(self) -> None:
        super().__init__("INVALID_CREDENTIALS", "Email or password is incorrect.")


class InvalidRefreshToken(AuthError):
    def __init__(self) -> None:
        super().__init__("INVALID_REFRESH_TOKEN", "Refresh token is invalid or expired.")


class SessionRevoked(AuthError):
    def __init__(self) -> None:
        super().__init__("SESSION_REVOKED", "Session has been revoked.")


# --- Result types ---


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    user_id: str
    tenant_id: str
    email: str


# --- Internal helpers ---


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _write_audit(
    db: AsyncSession,
    event_type: str,
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    session_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict | None = None,
) -> None:
    db.add(AuthAuditLog(
        audit_id=_prefixed_id("aud"),
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=session_id,
        event_type=event_type,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_=metadata,
    ))


async def _create_session_and_tokens(
    db: AsyncSession,
    user: User,
    ip_address: str | None,
    user_agent: str | None,
    refresh_ttl_s: int,
    access_ttl_s: int,
    issuer: str,
) -> tuple[AuthSession, str, str]:
    """Create a new session + initial refresh token + access token.

    Returns (session, access_token, opaque_refresh_token).
    """
    session = AuthSession(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    db.add(session)
    await db.flush()

    opaque_rt, rt_hash = generate_refresh_token()
    rt = RefreshToken(
        session_id=session.session_id,
        token_hash=rt_hash,
        expires_at=_now() + timedelta(seconds=refresh_ttl_s),
    )
    db.add(rt)

    access_token = create_access_token(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        session_id=session.session_id,
        issuer=issuer,
        ttl_seconds=access_ttl_s,
    )
    return session, access_token, opaque_rt


async def _revoke_session(
    db: AsyncSession,
    session: AuthSession,
    reason: str,
) -> None:
    session.revoked_at = _now()
    session.revoked_reason = reason
    await db.flush()


# --- Public API ---


async def create_user(
    db: AsyncSession,
    *,
    tenant_id: str,
    email: str,
    password: str,
) -> User:
    """Create a new user. Used internally and by tests — not exposed as a route."""
    user = User(
        tenant_id=tenant_id,
        email=email.strip().lower(),
        password_hash=hash_password(password),
    )
    db.add(user)
    await db.flush()
    _logger.info("user.created", user_id=user.user_id, tenant_id=tenant_id)
    return user


async def login(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenBundle:
    from app.secrets_config import get_config
    config = get_config()

    email = email.strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        dummy_verify()
        await _write_audit(
            db, "LOGIN_FAILED",
            ip_address=ip_address, user_agent=user_agent,
            metadata={"reason": "unknown_email"},
        )
        _logger.warning("auth.login_failed", reason="unknown_email")
        raise InvalidCredentials()

    if not user.is_active:
        dummy_verify()
        await _write_audit(
            db, "LOGIN_FAILED",
            user_id=user.user_id, tenant_id=user.tenant_id,
            ip_address=ip_address, user_agent=user_agent,
            metadata={"reason": "inactive_account"},
        )
        _logger.warning("auth.login_failed", user_id=user.user_id, reason="inactive_account")
        raise InvalidCredentials()

    if not verify_password(password, user.password_hash):
        await _write_audit(
            db, "LOGIN_FAILED",
            user_id=user.user_id, tenant_id=user.tenant_id,
            ip_address=ip_address, user_agent=user_agent,
            metadata={"reason": "wrong_password"},
        )
        _logger.warning("auth.login_failed", user_id=user.user_id, reason="wrong_password")
        raise InvalidCredentials()

    session, access_token, opaque_rt = await _create_session_and_tokens(
        db, user,
        ip_address=ip_address,
        user_agent=user_agent,
        refresh_ttl_s=config.auth.refresh_ttl_s,
        access_ttl_s=config.auth.access_ttl_s,
        issuer=config.auth.issuer,
    )

    await _write_audit(
        db, "LOGIN_SUCCESS",
        user_id=user.user_id, tenant_id=user.tenant_id,
        session_id=session.session_id,
        ip_address=ip_address, user_agent=user_agent,
    )
    _logger.info("auth.login_success", user_id=user.user_id, session_id=session.session_id)

    return TokenBundle(
        access_token=access_token,
        refresh_token=opaque_rt,
        expires_in=config.auth.access_ttl_s,
        refresh_expires_in=config.auth.refresh_ttl_s,
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        email=user.email,
    )


async def refresh(
    db: AsyncSession,
    *,
    refresh_token: str,
    ip_address: str | None = None,
) -> TokenBundle:
    from app.secrets_config import get_config
    config = get_config()

    token_hash = hash_refresh_token(refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()

    if rt is None:
        _logger.warning("auth.refresh_failed", reason="unknown_token")
        raise InvalidRefreshToken()

    # Load the session
    sess_result = await db.execute(
        select(AuthSession).where(AuthSession.session_id == rt.session_id)
    )
    session = sess_result.scalar_one_or_none()
    if session is None:
        raise InvalidRefreshToken()

    # Reuse detection: token already consumed → revoke entire session family
    if rt.consumed_at is not None:
        _logger.warning(
            "auth.refresh_reuse_detected",
            session_id=session.session_id,
            token_id=rt.token_id,
        )
        if session.revoked_at is None:
            await _revoke_session(db, session, "reuse")
        await _write_audit(
            db, "REUSE_DETECTED",
            user_id=session.user_id, tenant_id=session.tenant_id,
            session_id=session.session_id,
            ip_address=ip_address,
            metadata={"reused_token_id": rt.token_id},
        )
        raise InvalidRefreshToken()

    # Session already revoked
    if session.revoked_at is not None:
        _logger.warning("auth.refresh_revoked_session", session_id=session.session_id)
        await _write_audit(
            db, "REFRESH_REVOKED_SESSION",
            user_id=session.user_id, tenant_id=session.tenant_id,
            session_id=session.session_id,
            ip_address=ip_address,
        )
        raise SessionRevoked()

    # Expired
    if rt.expires_at < _now():
        _logger.warning("auth.refresh_expired", token_id=rt.token_id)
        raise InvalidRefreshToken()

    # Consume the current token
    rt.consumed_at = _now()
    rt.consumed_by_ip = ip_address

    # Issue new refresh token (child of current)
    new_opaque, new_hash = generate_refresh_token()
    new_rt = RefreshToken(
        session_id=session.session_id,
        parent_token_id=rt.token_id,
        token_hash=new_hash,
        expires_at=_now() + timedelta(seconds=config.auth.refresh_ttl_s),
    )
    db.add(new_rt)

    # Load user for the token bundle
    user_result = await db.execute(
        select(User).where(User.user_id == session.user_id)
    )
    user = user_result.scalar_one()

    access_token = create_access_token(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        session_id=session.session_id,
        issuer=config.auth.issuer,
        ttl_seconds=config.auth.access_ttl_s,
    )

    await _write_audit(
        db, "REFRESH",
        user_id=user.user_id, tenant_id=user.tenant_id,
        session_id=session.session_id,
        ip_address=ip_address,
    )
    _logger.info("auth.refresh_success", user_id=user.user_id, session_id=session.session_id)

    return TokenBundle(
        access_token=access_token,
        refresh_token=new_opaque,
        expires_in=config.auth.access_ttl_s,
        refresh_expires_in=config.auth.refresh_ttl_s,
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        email=user.email,
    )


async def logout(
    db: AsyncSession,
    *,
    refresh_token: str,
) -> None:
    """Always succeeds (204) to prevent session enumeration."""
    token_hash = hash_refresh_token(refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()
    if rt is None:
        return

    sess_result = await db.execute(
        select(AuthSession).where(AuthSession.session_id == rt.session_id)
    )
    session = sess_result.scalar_one_or_none()
    if session is None:
        return

    if session.revoked_at is None:
        await _revoke_session(db, session, "logout")
        await _write_audit(
            db, "LOGOUT",
            user_id=session.user_id, tenant_id=session.tenant_id,
            session_id=session.session_id,
        )
        _logger.info("auth.logout", user_id=session.user_id, session_id=session.session_id)


async def revoke_user_sessions(
    db: AsyncSession,
    *,
    user_id: str,
    reason: str,
) -> int:
    """Revoke all active sessions for a user. Returns count revoked."""
    result = await db.execute(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    )
    sessions = result.scalars().all()

    now = _now()
    for session in sessions:
        session.revoked_at = now
        session.revoked_reason = reason
        await _write_audit(
            db, "REVOKED",
            user_id=session.user_id, tenant_id=session.tenant_id,
            session_id=session.session_id,
            metadata={"reason": reason},
        )

    if sessions:
        await db.flush()
        _logger.info(
            "auth.sessions_revoked",
            user_id=user_id, count=len(sessions), reason=reason,
        )

    return len(sessions)


async def change_password(
    db: AsyncSession,
    *,
    user_id: str,
    new_password: str,
) -> None:
    """Change password and revoke all existing sessions."""
    result = await db.execute(select(User).where(User.user_id == user_id))
    user = result.scalar_one()

    user.password_hash = hash_password(new_password)
    user.password_changed_at = _now()

    revoked = await revoke_user_sessions(db, user_id=user_id, reason="password_change")
    _logger.info("auth.password_changed", user_id=user_id, sessions_revoked=revoked)


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(select(User).where(User.user_id == user_id))
    return result.scalar_one_or_none()

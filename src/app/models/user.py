"""Auth-related models: users, sessions, refresh tokens, auth audit log.

These tables are independent from the deal lifecycle tables. Primary keys
use prefixed string IDs (usr_, sess_, rt_, aud_) per the auth spec.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
    )

    user_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: _prefixed_id("usr")
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index(
            "idx_sessions_user_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    session_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: _prefixed_id("sess")
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        Index("idx_refresh_session", "session_id"),
    )

    token_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: _prefixed_id("rt")
    )
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), nullable=False
    )
    parent_token_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("refresh_tokens.token_id"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_by_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    session: Mapped["AuthSession"] = relationship(back_populates="refresh_tokens")


class AuthAuditLog(Base):
    __tablename__ = "auth_audit_log"

    audit_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: _prefixed_id("aud")
    )
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

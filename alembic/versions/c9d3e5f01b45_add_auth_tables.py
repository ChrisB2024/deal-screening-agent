"""add auth tables

Revision ID: c9d3e5f01b45
Revises: b7e2d4f89a23
Create Date: 2026-04-15 23:50:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c9d3e5f01b45"
down_revision: Union[str, None] = "b7e2d4f89a23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(32), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
    )
    op.create_index(
        "idx_sessions_user_active",
        "sessions",
        ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("token_id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.session_id"), nullable=False),
        sa.Column("parent_token_id", sa.String(64), sa.ForeignKey("refresh_tokens.token_id"), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_ip", sa.String(45), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("idx_refresh_session", "refresh_tokens", ["session_id"])

    op.create_table(
        "auth_audit_log",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("tenant_id", sa.String(64), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auth_audit_log")
    op.drop_index("idx_refresh_session", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("idx_sessions_user_active", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")

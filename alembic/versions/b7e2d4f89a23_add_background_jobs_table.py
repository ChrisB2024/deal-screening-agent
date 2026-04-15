"""add background_jobs table

Revision ID: b7e2d4f89a23
Revises: a3f1b9c45d01
Create Date: 2026-04-13 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b7e2d4f89a23"
down_revision: Union[str, Sequence[str], None] = "a3f1b9c45d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "not_before",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.String(64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(256), nullable=True),
        sa.Column("trace_context", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("job_type", "idempotency_key", name="uq_idempotency"),
        sa.CheckConstraint(
            "state IN ('PENDING','CLAIMED','RUNNING','SUCCEEDED','FAILED','DEAD_LETTERED')",
            name="valid_state",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts",
            name="attempts_bounded",
        ),
    )

    op.create_index(
        "idx_jobs_claim",
        "background_jobs",
        ["state", "not_before"],
        postgresql_where=sa.text("state = 'PENDING'"),
    )
    op.create_index("idx_jobs_type_state", "background_jobs", ["job_type", "state"])
    op.create_index("idx_jobs_tenant", "background_jobs", ["tenant_id", "created_at"])
    op.create_index(
        "idx_jobs_dead_letter",
        "background_jobs",
        ["dead_lettered_at"],
        postgresql_where=sa.text("state = 'DEAD_LETTERED'"),
    )


def downgrade() -> None:
    op.drop_index("idx_jobs_dead_letter", table_name="background_jobs")
    op.drop_index("idx_jobs_tenant", table_name="background_jobs")
    op.drop_index("idx_jobs_type_state", table_name="background_jobs")
    op.drop_index("idx_jobs_claim", table_name="background_jobs")
    op.drop_table("background_jobs")

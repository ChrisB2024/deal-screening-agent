"""audit log redesign and ARCHIVED status

Revision ID: a3f1b9c45d01
Revises: 1ed7e8f2e432
Create Date: 2026-04-13 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a3f1b9c45d01"
down_revision: Union[str, Sequence[str], None] = "1ed7e8f2e432"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_STATUSES = "('UPLOADED', 'EXTRACTED', 'FAILED', 'SCORED', 'DECIDED')"
NEW_STATUSES = "('UPLOADED', 'EXTRACTED', 'FAILED', 'SCORED', 'DECIDED', 'ARCHIVED')"


def upgrade() -> None:
    # --- 1. Replace audit_logs with deal_audit_log ---
    op.drop_index("ix_audit_logs_tenant_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_deal_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.create_table(
        "deal_audit_log",
        sa.Column("audit_id", sa.String(64), nullable=False),
        sa.Column("deal_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("before_state", sa.String(32), nullable=True),
        sa.Column("after_state", sa.String(32), nullable=True),
        sa.Column("metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index("ix_deal_audit_log_deal_id", "deal_audit_log", ["deal_id"])
    op.create_index("ix_deal_audit_log_tenant_id", "deal_audit_log", ["tenant_id"])

    # --- 2. Add ARCHIVED to DealStatus check constraint on deals.status ---
    # Constraint name is auto-generated; find and drop it dynamically.
    op.execute("""
        DO $$
        DECLARE cname TEXT;
        BEGIN
            SELECT conname INTO cname FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            WHERE t.relname = 'deals' AND c.contype = 'c'
            AND pg_get_constraintdef(c.oid) LIKE '%status%';
            IF cname IS NOT NULL THEN
                EXECUTE format('ALTER TABLE deals DROP CONSTRAINT %I', cname);
            END IF;
        END $$;
    """)
    op.create_check_constraint(
        "ck_deals_status",
        "deals",
        f"status IN {NEW_STATUSES}",
    )


def downgrade() -> None:
    # --- Revert DealStatus constraint ---
    op.drop_constraint("ck_deals_status", "deals", type_="check")
    op.create_check_constraint(
        "ck_deals_status",
        "deals",
        f"status IN {OLD_STATUSES}",
    )

    # --- Revert deal_audit_log back to audit_logs ---
    op.drop_index("ix_deal_audit_log_tenant_id", table_name="deal_audit_log")
    op.drop_index("ix_deal_audit_log_deal_id", table_name="deal_audit_log")
    op.drop_table("deal_audit_log")

    op.create_table(
        "audit_logs",
        sa.Column("deal_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "DEAL_UPLOADED",
                "EXTRACTION_STARTED",
                "EXTRACTION_COMPLETED",
                "EXTRACTION_FAILED",
                "SCORING_COMPLETED",
                "DECISION_MADE",
                "CRITERIA_UPDATED",
                "DEAL_RETRIED",
                name="auditaction",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "from_status",
            sa.Enum(*OLD_STATUSES.strip("()").replace("'", "").split(", "), name="dealstatus", native_enum=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            sa.Enum(*OLD_STATUSES.strip("()").replace("'", "").split(", "), name="dealstatus", native_enum=False),
            nullable=True,
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_deal_id", "audit_logs", ["deal_id"])
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])

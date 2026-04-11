"""Criteria configuration models — the user's investment thesis encoded as rules.

The scoring engine evaluates extracted deal fields against these criteria.
Criteria configs are versioned and tenant-scoped. When criteria change,
EXTRACTED deals are re-scored automatically (DECIDED deals are untouched).
"""

import uuid

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CriterionType


class CriteriaConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A versioned set of screening criteria for a tenant.

    Each config is immutable once created — updates create a new version.
    This preserves audit trail: every score references the exact criteria it was evaluated against.
    """

    __tablename__ = "criteria_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_criteria_configs_tenant_version"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="Default")

    criteria: Mapped[list["Criterion"]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )


class Criterion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single screening criterion within a config.

    Examples:
    - MUST_HAVE: sector in ["healthcare", "tech"], weight=1.0
    - DEALBREAKER: revenue < 1M
    - NICE_TO_HAVE: geography == "US Southeast", weight=0.5
    """

    __tablename__ = "criteria"

    config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("criteria_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # What field this criterion evaluates
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)

    # Rule definition
    criterion_type: Mapped[CriterionType] = mapped_column(
        Enum(CriterionType, native_enum=False), nullable=False
    )
    operator: Mapped[str] = mapped_column(String(16), nullable=False)  # eq, ne, gt, lt, gte, lte, in, not_in, contains
    target_value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-encoded target

    # Scoring weight (0.0–1.0). DEALBREAKER weight is effectively infinite (auto-fail).
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # Human-readable label shown in rationale
    label: Mapped[str] = mapped_column(String(256), nullable=False)

    config: Mapped["CriteriaConfig"] = relationship(back_populates="criteria")

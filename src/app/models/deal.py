"""Deal lifecycle models — maps directly to the spec state machine.

States: UPLOADED → EXTRACTED → SCORED → DECIDED (or UPLOADED → FAILED → retry → UPLOADED)
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AuditAction,
    ConfidenceLevel,
    DealStatus,
    DecisionType,
    FieldExtractionStatus,
)


class Deal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Core deal record. One row per ingested document.

    Invariants (from spec):
    - Every document gets a unique deal_id (UUID PK)
    - Duplicate detection by content_hash
    - Status transitions are validated (see enums.py state machine)
    """

    __tablename__ = "deals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "content_hash", name="uq_deals_tenant_content_hash"),
    )

    # Tenant isolation — every query must be scoped to tenant_id
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # Document metadata
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Source tracking
    source_channel: Mapped[str] = mapped_column(String(64), nullable=False, default="upload")
    source_sender: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # State machine
    status: Mapped[DealStatus] = mapped_column(
        Enum(DealStatus, native_enum=False),
        nullable=False,
        default=DealStatus.UPLOADED,
        index=True,
    )

    # Retry tracking for FAILED → UPLOADED
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    extracted_fields: Mapped[list["ExtractedField"]] = relationship(
        back_populates="deal", cascade="all, delete-orphan"
    )
    scores: Mapped[list["DealScore"]] = relationship(
        back_populates="deal", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["DealDecision"]] = relationship(
        back_populates="deal", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="deal", cascade="all, delete-orphan"
    )


class ExtractedField(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Structured fields extracted from a deal document by the LLM.

    Invariants (from spec):
    - Never fabricate data. Every field has FOUND / INFERRED / MISSING status.
    - Extraction proceeds if >= 3/6 core fields are present.
    - One row per field per extraction run (supports re-extraction).
    """

    __tablename__ = "extracted_fields"

    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extraction_run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Field data
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    field_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_status: Mapped[FieldExtractionStatus] = mapped_column(
        Enum(FieldExtractionStatus, native_enum=False), nullable=False
    )
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel, native_enum=False), nullable=False
    )

    # Overall extraction metadata (denormalized on the first field row per run for convenience)
    overall_confidence: Mapped[ConfidenceLevel | None] = mapped_column(
        Enum(ConfidenceLevel, native_enum=False), nullable=True
    )

    deal: Mapped["Deal"] = relationship(back_populates="extracted_fields")


class DealScore(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Score produced by the scoring engine for a deal.

    Invariants (from spec):
    - Score is deterministic for same inputs.
    - Rationale must cite specific criteria matches/misses.
    - Missing fields reduce confidence, never silently skipped.
    - Score history is append-only — re-scores create new records.
    """

    __tablename__ = "deal_scores"

    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    criteria_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("criteria_configs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Score output
    score: Mapped[int] = mapped_column(Integer, nullable=False)  # 0–100
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel, native_enum=False), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    # Per-criterion breakdown stored as JSON array
    # [{criterion_name, matched: bool, detail: str, weight: float}]
    criterion_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    deal: Mapped["Deal"] = relationship(back_populates="scores")


class DealDecision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Analyst's pass/pursue decision on a scored deal.

    Invariants (from spec):
    - Decisions are append-only (never edited, only superseded).
    - DECIDED → SCORED must never happen — decisions are immutable audit records.
    """

    __tablename__ = "deal_decisions"

    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    decision: Mapped[DecisionType] = mapped_column(
        Enum(DecisionType, native_enum=False), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reference which score the decision was made against
    score_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deal_scores.id", ondelete="SET NULL"), nullable=True
    )

    deal: Mapped["Deal"] = relationship(back_populates="decisions")


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Append-only audit trail for all deal state transitions.

    Invariant (from spec):
    - No state transition should occur without a corresponding audit log entry.
    """

    __tablename__ = "audit_logs"

    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, native_enum=False), nullable=False
    )
    from_status: Mapped[DealStatus | None] = mapped_column(
        Enum(DealStatus, native_enum=False), nullable=True
    )
    to_status: Mapped[DealStatus | None] = mapped_column(
        Enum(DealStatus, native_enum=False), nullable=True
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    deal: Mapped["Deal"] = relationship(back_populates="audit_logs")

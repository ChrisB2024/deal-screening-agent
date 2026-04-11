"""Pydantic schemas for deal-related API contracts.

These are the data shapes that cross module boundaries:
- Ingestion → Extraction: DealForExtraction
- Extraction → Scoring: ExtractedDeal
- Scoring → Dashboard: ScoredDeal
- Dashboard → User: DealCard
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    ConfidenceLevel,
    DealStatus,
    DecisionType,
    FieldExtractionStatus,
)


# --- Extraction schemas ---


class ExtractedFieldSchema(BaseModel):
    field_name: str
    field_value: str | None
    field_status: FieldExtractionStatus
    confidence: ConfidenceLevel


class ExtractedDealSchema(BaseModel):
    """Output of the extraction service. Input to the scoring engine."""

    deal_id: UUID
    fields: list[ExtractedFieldSchema]
    overall_confidence: ConfidenceLevel
    fields_found_count: int = Field(ge=0, le=6)
    extraction_run: int = 1


# --- Scoring schemas ---


class CriterionResultSchema(BaseModel):
    criterion_label: str
    field_name: str
    matched: bool
    detail: str
    weight: float


class ScoredDealSchema(BaseModel):
    """Output of the scoring engine. What the dashboard reads."""

    deal_id: UUID
    score: int = Field(ge=0, le=100)
    confidence: ConfidenceLevel
    rationale: str
    criterion_results: list[CriterionResultSchema]
    criteria_config_id: UUID | None = None


# --- Deal card for dashboard ---


class DealCardSchema(BaseModel):
    """Full deal view for the analyst dashboard."""

    id: UUID
    filename: str
    status: DealStatus
    source_channel: str
    created_at: datetime

    # Extraction summary
    extracted_fields: list[ExtractedFieldSchema] | None = None
    extraction_confidence: ConfidenceLevel | None = None

    # Latest score
    score: int | None = None
    score_confidence: ConfidenceLevel | None = None
    rationale: str | None = None

    # Decision (if any)
    decision: DecisionType | None = None
    decision_notes: str | None = None
    decided_at: datetime | None = None


# --- Request schemas ---


class DealUploadResponse(BaseModel):
    deal_id: UUID
    status: DealStatus
    message: str
    is_duplicate: bool = False


class DealDecisionRequest(BaseModel):
    decision: DecisionType
    notes: str | None = None

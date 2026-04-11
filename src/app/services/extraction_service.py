"""Extraction service — orchestrates the full deal extraction pipeline.

Pipeline: PDF → text → PII scrub → LLM extraction → validate → persist.

This is the module that transitions deals from UPLOADED → EXTRACTED (or FAILED).
Every state transition produces an audit log entry per spec invariant.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal import AuditLog, Deal, ExtractedField
from app.models.enums import (
    AuditAction,
    ConfidenceLevel,
    DealStatus,
    FieldExtractionStatus,
    CORE_EXTRACTION_FIELDS,
    MIN_FIELDS_FOR_EXTRACTION,
)
from app.services.llm_client import LLMExtractionError, extract_fields_via_llm
from app.services.pdf_parser import PDFParseError, extract_text_from_pdf
from app.services.pii_scrubber import scrub_pii

logger = logging.getLogger(__name__)


class ExtractionResult:
    """Result of an extraction attempt."""

    def __init__(
        self,
        success: bool,
        deal_id: uuid.UUID,
        fields_found: int = 0,
        overall_confidence: ConfidenceLevel = ConfidenceLevel.NONE,
        error: str | None = None,
    ):
        self.success = success
        self.deal_id = deal_id
        self.fields_found = fields_found
        self.overall_confidence = overall_confidence
        self.error = error


async def extract_deal(
    db: AsyncSession,
    deal_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> ExtractionResult:
    """Run the full extraction pipeline for a deal.

    Purpose: Orchestrate PDF parsing → PII scrub → LLM extraction → DB persistence.
    Inputs: Database session, deal_id, tenant_id (for audit trail).
    Outputs: ExtractionResult with success/failure and metadata.
    Invariants:
        - Deal must be in UPLOADED or FAILED status to extract.
        - State transitions always produce an audit log entry.
        - Extraction is atomic — either fully persisted or rolled back.
        - >= 3/6 fields → EXTRACTED, < 3 → FAILED.
    Security: PII scrubbed before LLM call. Tenant_id verified against deal.
    """
    # 1. Load the deal and verify state
    deal = await _load_and_verify_deal(db, deal_id, tenant_id)

    # 2. Log extraction start
    await _log_audit(db, deal, AuditAction.EXTRACTION_STARTED, deal.status, deal.status)

    try:
        # 3. Extract text from PDF
        raw_text = extract_text_from_pdf(deal.file_path)

        # 4. Scrub PII before sending to LLM
        scrubbed_text = scrub_pii(raw_text)

        # 5. Call LLM for structured extraction
        llm_result = await extract_fields_via_llm(scrubbed_text)

        # 6. Process and persist results
        return await _process_extraction_result(db, deal, llm_result)

    except (PDFParseError, LLMExtractionError, FileNotFoundError) as e:
        logger.error(f"Extraction failed for deal {deal_id}: {e}")
        return await _mark_deal_failed(db, deal, str(e))

    except Exception as e:
        logger.exception(f"Unexpected error extracting deal {deal_id}")
        return await _mark_deal_failed(db, deal, f"Unexpected error: {e}")


async def _load_and_verify_deal(
    db: AsyncSession,
    deal_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Deal:
    """Load a deal and verify it's in a valid state for extraction."""
    stmt = select(Deal).where(Deal.id == deal_id, Deal.tenant_id == tenant_id)
    result = await db.execute(stmt)
    deal = result.scalar_one_or_none()

    if deal is None:
        raise ValueError(f"Deal {deal_id} not found for tenant {tenant_id}")

    if deal.status not in (DealStatus.UPLOADED, DealStatus.FAILED):
        raise ValueError(
            f"Deal {deal_id} is in status {deal.status} — "
            "extraction only allowed from UPLOADED or FAILED"
        )

    return deal


async def _process_extraction_result(
    db: AsyncSession,
    deal: Deal,
    llm_result: dict[str, Any],
) -> ExtractionResult:
    """Validate LLM output, persist extracted fields, update deal status.

    This is where we enforce the >= 3/6 threshold from the spec.
    """
    fields = llm_result["fields"]
    extraction_run = deal.retry_count + 1

    # Count fields with actual values (FOUND or INFERRED)
    found_count = sum(
        1 for f in fields if f["field_status"] in ("FOUND", "INFERRED")
    )

    # Compute overall confidence
    overall_confidence = _compute_overall_confidence(fields, found_count)

    # Persist extracted fields
    for i, field_data in enumerate(fields):
        extracted_field = ExtractedField(
            deal_id=deal.id,
            extraction_run=extraction_run,
            field_name=field_data["field_name"],
            field_value=field_data.get("field_value"),
            field_status=FieldExtractionStatus(field_data["field_status"]),
            confidence=ConfidenceLevel(field_data["confidence"]),
            overall_confidence=overall_confidence if i == 0 else None,
        )
        db.add(extracted_field)

    # Decide: EXTRACTED or FAILED based on field count threshold
    if found_count >= MIN_FIELDS_FOR_EXTRACTION:
        old_status = deal.status
        deal.status = DealStatus.EXTRACTED
        await _log_audit(
            db, deal, AuditAction.EXTRACTION_COMPLETED, old_status, DealStatus.EXTRACTED
        )
        await db.flush()

        return ExtractionResult(
            success=True,
            deal_id=deal.id,
            fields_found=found_count,
            overall_confidence=overall_confidence,
        )
    else:
        return await _mark_deal_failed(
            db,
            deal,
            f"Only {found_count}/{len(CORE_EXTRACTION_FIELDS)} fields extracted "
            f"(minimum {MIN_FIELDS_FOR_EXTRACTION} required)",
        )


def _compute_overall_confidence(
    fields: list[dict[str, Any]], found_count: int
) -> ConfidenceLevel:
    """Derive overall extraction confidence from per-field results.

    Rules:
    - 6/6 fields FOUND with HIGH confidence → HIGH
    - >= 4 fields with >= MEDIUM confidence → MEDIUM
    - >= 3 fields (minimum threshold) → LOW
    - < 3 fields → NONE
    """
    if found_count < MIN_FIELDS_FOR_EXTRACTION:
        return ConfidenceLevel.NONE

    high_count = sum(
        1
        for f in fields
        if f["field_status"] in ("FOUND", "INFERRED") and f["confidence"] == "HIGH"
    )

    if found_count == len(CORE_EXTRACTION_FIELDS) and high_count == found_count:
        return ConfidenceLevel.HIGH

    medium_or_better = sum(
        1
        for f in fields
        if f["field_status"] in ("FOUND", "INFERRED")
        and f["confidence"] in ("HIGH", "MEDIUM")
    )

    if medium_or_better >= 4:
        return ConfidenceLevel.MEDIUM

    return ConfidenceLevel.LOW


async def _mark_deal_failed(
    db: AsyncSession,
    deal: Deal,
    error_detail: str,
) -> ExtractionResult:
    """Transition deal to FAILED status with audit log."""
    old_status = deal.status
    deal.status = DealStatus.FAILED
    deal.retry_count += 1

    await _log_audit(
        db, deal, AuditAction.EXTRACTION_FAILED, old_status, DealStatus.FAILED, error_detail
    )
    await db.flush()

    return ExtractionResult(
        success=False,
        deal_id=deal.id,
        error=error_detail,
    )


async def _log_audit(
    db: AsyncSession,
    deal: Deal,
    action: AuditAction,
    from_status: DealStatus | None,
    to_status: DealStatus | None,
    detail: str | None = None,
) -> None:
    """Create an audit log entry. Spec invariant: no state transition without audit."""
    audit = AuditLog(
        deal_id=deal.id,
        tenant_id=deal.tenant_id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        detail=detail,
    )
    db.add(audit)

"""Deals API routes — upload, list, detail, decide.

Spec module: Dashboard API + UI (API portion).
All routes require tenant context. All responses include request_id.
"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_request_id, get_tenant_id, get_user_id
from app.database import get_db
from app.models.deal import AuditLog, Deal, DealDecision, DealScore, ExtractedField
from app.models.enums import (
    AuditAction,
    ConfidenceLevel,
    DealStatus,
    DecisionType,
    FieldExtractionStatus,
)
from app.schemas.deal import (
    DealCardSchema,
    DealDecisionRequest,
    DealUploadResponse,
    ExtractedFieldSchema,
)
from app.services.ingestion_service import IngestionError, ingest_deal

router = APIRouter()


@router.post("/upload", response_model=DealUploadResponse)
async def upload_deal(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    request_id: str = Depends(get_request_id),
):
    """Upload a deal document (PDF) for screening.

    Runs the full pipeline: ingest → extract → score.
    Returns the deal_id, status, and a summary message.
    """
    content = await file.read()

    try:
        result = await ingest_deal(
            db=db,
            tenant_id=tenant_id,
            filename=file.filename or "unknown.pdf",
            file_content=content,
            content_type=file.content_type,
            source_channel="upload",
        )
    except IngestionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return DealUploadResponse(
        deal_id=result.deal_id,
        status=result.status,
        message=result.message,
        is_duplicate=result.is_duplicate,
    )


@router.get("/", response_model=list[DealCardSchema])
async def list_deals(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    status: DealStatus | None = Query(None, description="Filter by deal status"),
    sort_by: str = Query("created_at", description="Sort field: created_at or score"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List deals for the tenant with optional filtering and sorting.

    Returns deal cards with extraction summary and latest score.
    Spec invariant: Dashboard surfaces the ranked deal queue.
    """
    stmt = select(Deal).where(Deal.tenant_id == tenant_id)

    if status is not None:
        stmt = stmt.where(Deal.status == status)

    if sort_by == "score":
        # Join with latest score for sorting
        score_subq = (
            select(
                DealScore.deal_id,
                func.max(DealScore.score).label("max_score"),
            )
            .group_by(DealScore.deal_id)
            .subquery()
        )
        stmt = stmt.outerjoin(score_subq, Deal.id == score_subq.c.deal_id)
        order_col = score_subq.c.max_score
    else:
        order_col = Deal.created_at

    if sort_order == "asc":
        stmt = stmt.order_by(order_col.asc().nulls_last())
    else:
        stmt = stmt.order_by(order_col.desc().nulls_last())

    stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    deals = result.scalars().all()

    # Build deal cards with related data
    cards = []
    for deal in deals:
        card = await _build_deal_card(db, deal)
        cards.append(card)

    return cards


@router.get("/{deal_id}", response_model=DealCardSchema)
async def get_deal(
    deal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get full detail for a single deal."""
    deal = await _get_deal_or_404(db, deal_id, tenant_id)
    return await _build_deal_card(db, deal)


@router.post("/{deal_id}/decide")
async def decide_deal(
    deal_id: uuid.UUID,
    body: DealDecisionRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID = Depends(get_user_id),
    request_id: str = Depends(get_request_id),
):
    """Record a pass/pursue decision on a scored deal.

    Spec invariants:
    - Deal must be in SCORED status (score must be visible before deciding).
    - Decisions are append-only (never edited, only superseded).
    - DECIDED → SCORED must never happen.
    """
    deal = await _get_deal_or_404(db, deal_id, tenant_id)

    if deal.status != DealStatus.SCORED:
        raise HTTPException(
            status_code=400,
            detail=f"Deal is in status {deal.status.value}. Decisions can only be made on SCORED deals.",
        )

    # Get latest score for reference
    latest_score = await _get_latest_score(db, deal_id)

    # Create decision (append-only)
    decision = DealDecision(
        deal_id=deal.id,
        user_id=user_id,
        decision=body.decision,
        notes=body.notes,
        score_id=latest_score.id if latest_score else None,
    )
    db.add(decision)

    # Transition state
    old_status = deal.status
    deal.status = DealStatus.DECIDED

    # Audit log
    audit = AuditLog(
        deal_id=deal.id,
        tenant_id=tenant_id,
        user_id=user_id,
        action=AuditAction.DECISION_MADE,
        from_status=old_status,
        to_status=DealStatus.DECIDED,
        detail=f"Decision: {body.decision.value}. Notes: {body.notes or 'None'}",
    )
    db.add(audit)
    await db.flush()

    return {
        "deal_id": str(deal.id),
        "decision": body.decision.value,
        "status": deal.status.value,
        "message": f"Deal marked as {body.decision.value}.",
        "request_id": request_id,
    }


# --- Helper functions ---


async def _get_deal_or_404(
    db: AsyncSession,
    deal_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Deal:
    """Load a deal scoped to tenant, or raise 404."""
    stmt = select(Deal).where(Deal.id == deal_id, Deal.tenant_id == tenant_id)
    result = await db.execute(stmt)
    deal = result.scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found.")
    return deal


async def _get_latest_score(
    db: AsyncSession,
    deal_id: uuid.UUID,
) -> DealScore | None:
    """Get the most recent score for a deal."""
    stmt = (
        select(DealScore)
        .where(DealScore.deal_id == deal_id)
        .order_by(DealScore.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _get_latest_decision(
    db: AsyncSession,
    deal_id: uuid.UUID,
) -> DealDecision | None:
    """Get the most recent decision for a deal."""
    stmt = (
        select(DealDecision)
        .where(DealDecision.deal_id == deal_id)
        .order_by(DealDecision.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _get_latest_extracted_fields(
    db: AsyncSession,
    deal_id: uuid.UUID,
) -> list[ExtractedField]:
    """Get the latest extraction run's fields."""
    stmt = (
        select(ExtractedField)
        .where(ExtractedField.deal_id == deal_id)
        .order_by(ExtractedField.extraction_run.desc())
    )
    result = await db.execute(stmt)
    all_fields = result.scalars().all()

    if not all_fields:
        return []

    latest_run = max(f.extraction_run for f in all_fields)
    return [f for f in all_fields if f.extraction_run == latest_run]


async def _build_deal_card(db: AsyncSession, deal: Deal) -> DealCardSchema:
    """Build a full deal card with extraction, score, and decision data."""
    # Extracted fields
    fields = await _get_latest_extracted_fields(db, deal.id)
    extracted_fields = None
    extraction_confidence = None
    if fields:
        extracted_fields = [
            ExtractedFieldSchema(
                field_name=f.field_name,
                field_value=f.field_value,
                field_status=f.field_status,
                confidence=f.confidence,
            )
            for f in fields
        ]
        # Get overall confidence from the first field that has it
        for f in fields:
            if f.overall_confidence is not None:
                extraction_confidence = f.overall_confidence
                break

    # Latest score
    latest_score = await _get_latest_score(db, deal.id)

    # Latest decision
    latest_decision = await _get_latest_decision(db, deal.id)

    return DealCardSchema(
        id=deal.id,
        filename=deal.filename,
        status=deal.status,
        source_channel=deal.source_channel,
        created_at=deal.created_at,
        extracted_fields=extracted_fields,
        extraction_confidence=extraction_confidence,
        score=latest_score.score if latest_score else None,
        score_confidence=latest_score.confidence if latest_score else None,
        rationale=latest_score.rationale if latest_score else None,
        decision=latest_decision.decision if latest_decision else None,
        decision_notes=latest_decision.notes if latest_decision else None,
        decided_at=latest_decision.created_at if latest_decision else None,
    )

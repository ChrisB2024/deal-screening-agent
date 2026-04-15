"""Deals API routes — upload, list, detail, decide.

Spec module: Dashboard API + UI (API portion).
All routes require tenant context. All responses include request_id.
"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import AuthContext, get_request_id, require_auth
from app.database import get_db
from app.input_validation import validate_file, validate_pdf, ValidationFailure
from app.models.deal import DealAuditLog, Deal, DealDecision, DealScore, ExtractedField
from app.models.enums import (
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


@router.post("/upload", response_model=DealUploadResponse, status_code=202)
async def upload_deal(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    tenant_id = uuid.UUID(auth.tenant_id)
    content = await file.read()

    file_check = validate_file(content, file.content_type)
    if isinstance(file_check, ValidationFailure):
        raise HTTPException(
            status_code=file_check.http_status,
            detail=file_check.user_message,
        )

    pdf_check = validate_pdf(file_check)
    if isinstance(pdf_check, ValidationFailure):
        raise HTTPException(
            status_code=pdf_check.http_status,
            detail=pdf_check.user_message,
        )

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
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    status: DealStatus | None = Query(None, description="Filter by deal status"),
    sort_by: str = Query("created_at", description="Sort field: created_at or score"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    tenant_id = uuid.UUID(auth.tenant_id)
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
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(auth.tenant_id)
    deal = await _get_deal_or_404(db, deal_id, tenant_id)
    return await _build_deal_card(db, deal)


@router.post("/{deal_id}/decide")
async def decide_deal(
    deal_id: uuid.UUID,
    body: DealDecisionRequest,
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    tenant_id = uuid.UUID(auth.tenant_id)
    user_id = auth.user_id
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
    audit = DealAuditLog(
        audit_id=str(uuid.uuid4()),
        deal_id=deal.id,
        tenant_id=str(tenant_id),
        actor_type="user",
        actor_id=user_id,
        action="DECISION_MADE",
        before_state=old_status.value,
        after_state=DealStatus.DECIDED.value,
        metadata_={"decision": body.decision.value, "notes": body.notes},
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

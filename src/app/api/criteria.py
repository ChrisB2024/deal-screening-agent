"""Criteria config API routes — create and manage screening criteria.

Spec module: Dashboard API + UI (criteria configuration portion).
Criteria configs are immutable and versioned. Creating a new config
deactivates the previous one and bumps the version.

Spec invariant: "Criteria changes trigger re-score of EXTRACTED deals only."
Re-scoring is triggered on config creation.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_request_id, get_tenant_id
from app.database import get_db
from app.models.criteria import CriteriaConfig, Criterion
from app.models.deal import Deal
from app.models.enums import DealStatus
from app.schemas.criteria import (
    CriteriaConfigCreateSchema,
    CriteriaConfigResponseSchema,
    CriterionResponseSchema,
)
from app.services.scoring_service import score_deal

router = APIRouter()


@router.post("/config", response_model=CriteriaConfigResponseSchema, status_code=201)
async def create_criteria_config(
    body: CriteriaConfigCreateSchema,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    request_id: str = Depends(get_request_id),
):
    """Create a new criteria config, deactivating the previous one.

    Immutable versioning: each new config increments the version number.
    The old config is deactivated but preserved for audit trail.
    Triggers re-scoring of all EXTRACTED deals.
    """
    # Get current version number
    latest_version = await _get_latest_version(db, tenant_id)
    new_version = latest_version + 1

    # Deactivate all existing configs for this tenant
    await db.execute(
        update(CriteriaConfig)
        .where(CriteriaConfig.tenant_id == tenant_id, CriteriaConfig.is_active == True)  # noqa: E712
        .values(is_active=False)
    )

    # Create new config
    config = CriteriaConfig(
        tenant_id=tenant_id,
        version=new_version,
        is_active=True,
        name=body.name,
    )
    db.add(config)
    await db.flush()  # Get config.id for criteria FK

    # Create criteria
    for criterion_data in body.criteria:
        criterion = Criterion(
            config_id=config.id,
            field_name=criterion_data.field_name,
            criterion_type=criterion_data.criterion_type,
            operator=criterion_data.operator,
            target_value=criterion_data.target_value,
            weight=criterion_data.weight,
            label=criterion_data.label,
        )
        db.add(criterion)

    await db.flush()

    # Trigger re-scoring of EXTRACTED deals (spec invariant)
    await _rescore_extracted_deals(db, tenant_id)

    # Reload with criteria for response
    stmt = (
        select(CriteriaConfig)
        .options(selectinload(CriteriaConfig.criteria))
        .where(CriteriaConfig.id == config.id)
    )
    result = await db.execute(stmt)
    config = result.scalar_one()

    return _config_to_response(config)


@router.get("/config", response_model=CriteriaConfigResponseSchema | None)
async def get_active_config(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get the currently active criteria config for the tenant."""
    stmt = (
        select(CriteriaConfig)
        .options(selectinload(CriteriaConfig.criteria))
        .where(
            CriteriaConfig.tenant_id == tenant_id,
            CriteriaConfig.is_active == True,  # noqa: E712
        )
        .order_by(CriteriaConfig.version.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if config is None:
        raise HTTPException(status_code=404, detail="No active criteria config found.")

    return _config_to_response(config)


@router.get("/config/history", response_model=list[CriteriaConfigResponseSchema])
async def get_config_history(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get all criteria config versions for audit trail."""
    stmt = (
        select(CriteriaConfig)
        .options(selectinload(CriteriaConfig.criteria))
        .where(CriteriaConfig.tenant_id == tenant_id)
        .order_by(CriteriaConfig.version.desc())
    )
    result = await db.execute(stmt)
    configs = result.scalars().all()
    return [_config_to_response(c) for c in configs]


# --- Helper functions ---


async def _get_latest_version(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Get the latest config version number for a tenant (0 if none exist)."""
    stmt = (
        select(CriteriaConfig.version)
        .where(CriteriaConfig.tenant_id == tenant_id)
        .order_by(CriteriaConfig.version.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    version = result.scalar_one_or_none()
    return version or 0


async def _rescore_extracted_deals(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Re-score all deals in EXTRACTED status for the tenant.

    Spec: "Criteria changes trigger re-score of EXTRACTED deals only."
    DECIDED deals are untouched — decisions are immutable audit records.
    """
    stmt = select(Deal.id).where(
        Deal.tenant_id == tenant_id,
        Deal.status == DealStatus.EXTRACTED,
    )
    result = await db.execute(stmt)
    deal_ids = result.scalars().all()

    for deal_id in deal_ids:
        try:
            await score_deal(db, deal_id, tenant_id)
        except Exception as e:
            # Log but don't fail the config creation
            import logging
            logging.getLogger(__name__).warning(
                f"Re-scoring deal {deal_id} failed: {e}"
            )


def _config_to_response(config: CriteriaConfig) -> CriteriaConfigResponseSchema:
    """Convert a CriteriaConfig model to its response schema."""
    return CriteriaConfigResponseSchema(
        id=config.id,
        tenant_id=config.tenant_id,
        version=config.version,
        is_active=config.is_active,
        name=config.name,
        criteria=[
            CriterionResponseSchema(
                id=c.id,
                field_name=c.field_name,
                criterion_type=c.criterion_type,
                operator=c.operator,
                target_value=c.target_value,
                weight=c.weight,
                label=c.label,
            )
            for c in config.criteria
        ],
    )

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.observability.logger import get_logger

from .registry import register
from .types import JobContext, TenantMismatch

_logger = get_logger("background_jobs.handlers")


class ExtractionJob(BaseModel):
    deal_id: str
    tenant_id: str


@register("extraction", schema=ExtractionJob, max_attempts=5)
async def handle_extraction(job: ExtractionJob, ctx: JobContext) -> None:
    from app.database import async_session_factory
    from app.services.extraction_service import extract_deal
    from app.services.scoring_service import score_deal

    deal_id = uuid.UUID(job.deal_id)
    tenant_id = uuid.UUID(job.tenant_id)

    if ctx.tenant_id and ctx.tenant_id != job.tenant_id:
        raise TenantMismatch(
            f"Job tenant_id {ctx.tenant_id} != payload tenant_id {job.tenant_id}"
        )

    async with async_session_factory() as session:
        async with session.begin():
            extraction_result = await extract_deal(session, deal_id, tenant_id)

        if not extraction_result.success:
            _logger.warning(
                "extraction.failed",
                deal_id=job.deal_id,
                error=extraction_result.error,
            )
            return

        async with session.begin():
            scoring_result = await score_deal(session, deal_id, tenant_id)

        if not scoring_result.success:
            _logger.warning(
                "scoring.failed",
                deal_id=job.deal_id,
                error=scoring_result.error,
            )

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logger import get_logger
from app.observability.scrubber import scrub_value

from .backoff import compute_backoff_seconds
from .models import BackgroundJob
from .registry import get_handler, get_max_attempts, is_registered
from .types import (
    NON_RETRYABLE_EXCEPTIONS,
    JobContext,
    JobState,
    SchemaViolation,
    UnknownJobType,
)

_logger = get_logger("background_jobs")

DEFAULT_CLAIM_TTL_SECONDS = 300


async def enqueue(
    db: AsyncSession,
    *,
    job_type: str,
    payload: dict,
    idempotency_key: str | None = None,
    trace_context: dict | None = None,
    not_before: datetime | None = None,
    tenant_id: str | None = None,
) -> str:
    if not is_registered(job_type):
        raise UnknownJobType(f"Job type not registered: {job_type}")

    reg = get_handler(job_type)
    try:
        reg.schema.model_validate(payload)
    except ValidationError as exc:
        raise SchemaViolation(f"Invalid payload for {job_type}: {exc}") from exc

    if tenant_id is None:
        tenant_id = payload.get("tenant_id")

    job_id = uuid.uuid4().hex

    stmt = pg_insert(BackgroundJob).values(
        job_id=job_id,
        job_type=job_type,
        payload=payload,
        state=JobState.PENDING.value,
        attempts=0,
        max_attempts=reg.max_attempts,
        not_before=not_before or datetime.now(timezone.utc),
        idempotency_key=idempotency_key,
        trace_context=trace_context,
        tenant_id=tenant_id,
    )

    if idempotency_key is not None:
        stmt = stmt.on_conflict_do_nothing(constraint="uq_idempotency")

    result = await db.execute(stmt)

    if result.rowcount == 0 and idempotency_key is not None:
        existing = await db.execute(
            select(BackgroundJob.job_id).where(
                BackgroundJob.job_type == job_type,
                BackgroundJob.idempotency_key == idempotency_key,
            )
        )
        existing_id = existing.scalar_one()
        _logger.info("jobs.enqueue.idempotent_hit", job_type=job_type, existing_job_id=existing_id)
        return existing_id

    _logger.info("jobs.enqueued", job_id=job_id, job_type=job_type, tenant_id=tenant_id)
    return job_id


async def claim(
    db: AsyncSession,
    worker_id: str,
    *,
    claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
) -> BackgroundJob | None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=claim_ttl_seconds)

    stmt = (
        select(BackgroundJob)
        .where(
            BackgroundJob.state == JobState.PENDING.value,
            BackgroundJob.not_before <= now,
        )
        .order_by(BackgroundJob.not_before.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if job is None:
        return None

    job.state = JobState.CLAIMED.value
    job.claimed_by = worker_id
    job.claim_expires_at = expires
    await db.flush()

    _logger.info("jobs.claimed", job_id=job.job_id, job_type=job.job_type, worker_id=worker_id)
    return job


async def mark_running(db: AsyncSession, job: BackgroundJob) -> None:
    job.state = JobState.RUNNING.value
    await db.flush()


async def mark_succeeded(db: AsyncSession, job: BackgroundJob) -> None:
    job.state = JobState.SUCCEEDED.value
    job.succeeded_at = datetime.now(timezone.utc)
    job.claimed_by = None
    job.claim_expires_at = None
    await db.flush()

    _logger.info(
        "jobs.succeeded",
        job_id=job.job_id,
        job_type=job.job_type,
        attempts=job.attempts,
    )


async def mark_failed(
    db: AsyncSession,
    job: BackgroundJob,
    error: str,
    *,
    non_retryable: bool = False,
) -> None:
    job.attempts += 1
    job.last_error = scrub_value(error[:4096])
    job.claimed_by = None
    job.claim_expires_at = None

    if non_retryable or job.attempts >= job.max_attempts:
        job.state = JobState.DEAD_LETTERED.value
        job.dead_lettered_at = datetime.now(timezone.utc)
        _logger.critical(
            "jobs.dead_lettered",
            job_id=job.job_id,
            job_type=job.job_type,
            attempts=job.attempts,
            reason="non_retryable" if non_retryable else "max_attempts_exceeded",
            last_error=error[:256],
        )
    else:
        backoff = compute_backoff_seconds(job.attempts - 1)
        job.state = JobState.PENDING.value
        job.not_before = datetime.now(timezone.utc) + timedelta(seconds=backoff)
        _logger.warning(
            "jobs.failed.retrying",
            job_id=job.job_id,
            job_type=job.job_type,
            attempt=job.attempts,
            retry_after_seconds=round(backoff, 1),
        )

    await db.flush()


async def reap_expired_claims(db: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    stmt = (
        update(BackgroundJob)
        .where(
            BackgroundJob.state.in_([JobState.CLAIMED.value, JobState.RUNNING.value]),
            BackgroundJob.claim_expires_at < now,
        )
        .values(
            state=JobState.PENDING.value,
            claimed_by=None,
            claim_expires_at=None,
        )
    )
    result = await db.execute(stmt)
    count = result.rowcount
    if count:
        _logger.warning("jobs.reaped_expired_claims", count=count)
    return count

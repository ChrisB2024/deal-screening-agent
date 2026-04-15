"""Admin CLI for dead-lettered job management.

Usage:
    python -m app.background_jobs.admin list-dead-letter [--job-type TYPE]
    python -m app.background_jobs.admin retry <job_id>
    python -m app.background_jobs.admin drop <job_id>
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.background_jobs.models import BackgroundJob
from app.background_jobs.queue import enqueue
from app.background_jobs.registry import is_registered
from app.background_jobs.types import JobState
from app.database import async_session_factory
from app.observability.logger import get_logger

_logger = get_logger("background_jobs.admin")


async def list_dead_letter(job_type: str | None = None) -> None:
    async with async_session_factory() as session:
        stmt = select(BackgroundJob).where(
            BackgroundJob.state == JobState.DEAD_LETTERED.value
        ).order_by(BackgroundJob.dead_lettered_at.desc())

        if job_type:
            stmt = stmt.where(BackgroundJob.job_type == job_type)

        result = await session.execute(stmt)
        jobs = result.scalars().all()

        if not jobs:
            print("No dead-lettered jobs found.")
            return

        print(f"{'JOB_ID':<36} {'TYPE':<16} {'ATTEMPTS':<10} {'DEAD_LETTERED_AT':<28} {'LAST_ERROR'}")
        print("-" * 120)
        for job in jobs:
            error = (job.last_error or "")[:60]
            dl_at = str(job.dead_lettered_at or "")
            print(f"{job.job_id:<36} {job.job_type:<16} {job.attempts:<10} {dl_at:<28} {error}")


async def retry_job(job_id: str) -> None:
    """Re-enqueue a dead-lettered job as a new job."""
    async with async_session_factory() as session:
        async with session.begin():
            stmt = select(BackgroundJob).where(
                BackgroundJob.job_id == job_id,
                BackgroundJob.state == JobState.DEAD_LETTERED.value,
            )
            result = await session.execute(stmt)
            job = result.scalar_one_or_none()

            if job is None:
                print(f"Job {job_id} not found or not in DEAD_LETTERED state.")
                return

            if not is_registered(job.job_type):
                print(f"Job type '{job.job_type}' is not registered. Cannot retry.")
                return

            # Create a new job with the same payload (immutable dead-letter row stays)
            new_id = await enqueue(
                session,
                job_type=job.job_type,
                payload=job.payload,
                trace_context=job.trace_context,
                tenant_id=job.tenant_id,
            )

        print(f"Retried dead-lettered job {job_id} → new job {new_id}")


async def drop_job(job_id: str) -> None:
    """Delete a dead-lettered job row."""
    async with async_session_factory() as session:
        async with session.begin():
            stmt = select(BackgroundJob).where(
                BackgroundJob.job_id == job_id,
                BackgroundJob.state == JobState.DEAD_LETTERED.value,
            )
            result = await session.execute(stmt)
            job = result.scalar_one_or_none()

            if job is None:
                print(f"Job {job_id} not found or not in DEAD_LETTERED state.")
                return

            await session.delete(job)

        print(f"Dropped dead-lettered job {job_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Background jobs admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    ls = sub.add_parser("list-dead-letter", help="List dead-lettered jobs")
    ls.add_argument("--job-type", help="Filter by job type")

    rt = sub.add_parser("retry", help="Retry a dead-lettered job (creates new job)")
    rt.add_argument("job_id", help="ID of the dead-lettered job")

    dr = sub.add_parser("drop", help="Delete a dead-lettered job")
    dr.add_argument("job_id", help="ID of the dead-lettered job")

    args = parser.parse_args()

    async def run() -> None:
        from app.secrets_config import bootstrap as sc_bootstrap
        await sc_bootstrap()
        from app.background_jobs import init_handlers
        init_handlers()

        if args.command == "list-dead-letter":
            await list_dead_letter(args.job_type)
        elif args.command == "retry":
            await retry_job(args.job_id)
        elif args.command == "drop":
            await drop_job(args.job_id)

    asyncio.run(run())


if __name__ == "__main__":
    main()

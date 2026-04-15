from __future__ import annotations

import asyncio
import signal
import uuid
from contextlib import asynccontextmanager

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.observability.logger import get_logger

from .queue import claim, mark_failed, mark_running, mark_succeeded, reap_expired_claims
from .registry import get_handler
from .types import NON_RETRYABLE_EXCEPTIONS, JobContext, JobState, SchemaViolation, UnknownJobType

_logger = get_logger("background_jobs.worker")

POLL_INTERVAL_SECONDS = 2.0
REAP_INTERVAL_SECONDS = 60.0


class Worker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        concurrency: int = 4,
        worker_id: str | None = None,
    ):
        self._session_factory = session_factory
        self._concurrency = concurrency
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        _logger.info(
            "worker.starting",
            worker_id=self._worker_id,
            concurrency=self._concurrency,
        )

        tasks = [
            asyncio.create_task(self._claim_loop(i))
            for i in range(self._concurrency)
        ]
        tasks.append(asyncio.create_task(self._reap_loop()))

        await self._shutdown.wait()

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        _logger.info("worker.stopped", worker_id=self._worker_id)

    def stop(self) -> None:
        self._shutdown.set()

    async def _claim_loop(self, slot: int) -> None:
        slot_id = f"{self._worker_id}:slot-{slot}"
        while not self._shutdown.is_set():
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        job = await claim(session, slot_id)

                    if job is None:
                        await asyncio.sleep(POLL_INTERVAL_SECONDS)
                        continue

                    await self._execute_job(session, job)
            except asyncio.CancelledError:
                return
            except Exception:
                _logger.exception("worker.claim_loop_error", slot=slot_id)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _execute_job(self, session: AsyncSession, job) -> None:
        try:
            reg = get_handler(job.job_type)
        except UnknownJobType:
            async with session.begin():
                await mark_failed(session, job, f"Unknown job type: {job.job_type}", non_retryable=True)
            return

        try:
            typed_payload = reg.schema.model_validate(job.payload)
        except ValidationError as exc:
            async with session.begin():
                await mark_failed(
                    session, job, f"Schema validation failed: {exc}", non_retryable=True
                )
            return

        ctx = JobContext(
            job_id=job.job_id,
            job_type=job.job_type,
            attempt=job.attempts + 1,
            trace_context=job.trace_context,
            tenant_id=job.tenant_id,
        )

        async with session.begin():
            await mark_running(session, job)

        try:
            await reg.handler(typed_payload, ctx)
        except NON_RETRYABLE_EXCEPTIONS as exc:
            async with session.begin():
                await mark_failed(session, job, str(exc), non_retryable=True)
            return
        except Exception as exc:
            async with session.begin():
                await mark_failed(session, job, str(exc), non_retryable=False)
            return

        async with session.begin():
            await mark_succeeded(session, job)

    async def _reap_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(REAP_INTERVAL_SECONDS)
                async with self._session_factory() as session:
                    async with session.begin():
                        await reap_expired_claims(session)
            except asyncio.CancelledError:
                return
            except Exception:
                _logger.exception("worker.reap_loop_error")

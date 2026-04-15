import asyncio
import importlib.util
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel
from pydantic import ValidationError

from app.background_jobs.backoff import compute_backoff_seconds
from app.background_jobs.registry import (
    HandlerRegistration,
    _registry,
    get_handler,
    is_registered,
    register,
    registered_types,
)
from app.background_jobs.types import (
    JobContext,
    JobState,
    NonRetryableError,
    SchemaViolation,
    TenantMismatch,
    UnknownJobType,
)


class _Result:
    def __init__(self, *, rowcount=None, scalar=None, scalars=None):
        self.rowcount = rowcount
        self._scalar = scalar
        self._scalars = scalars

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars or [])


class _BeginContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, execute_results=None):
        self._execute_results = list(execute_results or [])
        self.flush = AsyncMock()
        self.delete = AsyncMock()

    async def execute(self, stmt):
        if not self._execute_results:
            raise AssertionError("No fake execute result configured")
        result = self._execute_results.pop(0)
        return result(stmt) if callable(result) else result

    def begin(self):
        return _BeginContext()


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


# --- Backoff tests ---


def test_backoff_schedule_matches_spec():
    for attempt, expected_base in enumerate([1, 4, 16, 64, 256]):
        delay = compute_backoff_seconds(attempt, jitter_fraction=0.0)
        assert delay == expected_base


def test_backoff_jitter_within_bounds():
    for _ in range(100):
        delay = compute_backoff_seconds(0, jitter_fraction=0.25)
        assert 0.75 <= delay <= 1.25


def test_backoff_clamps_to_last_entry_for_high_attempts():
    delay = compute_backoff_seconds(99, jitter_fraction=0.0)
    assert delay == 256


# --- Registry tests ---


class _TestPayload(BaseModel):
    deal_id: str
    tenant_id: str


def test_register_and_lookup():
    key = f"test_type_{uuid.uuid4().hex[:8]}"

    @register(key, schema=_TestPayload, max_attempts=3)
    async def _handler(job, ctx):
        pass

    try:
        assert is_registered(key)
        reg = get_handler(key)
        assert reg.schema is _TestPayload
        assert reg.max_attempts == 3
        assert reg.handler is _handler
        assert key in registered_types()
    finally:
        _registry.pop(key, None)


def test_get_handler_raises_for_unregistered_type():
    with pytest.raises(UnknownJobType):
        get_handler("nonexistent_type_abc")


# --- Types tests ---


def test_job_context_is_frozen():
    ctx = JobContext(job_id="j1", job_type="extraction", attempt=1)
    with pytest.raises(AttributeError):
        ctx.job_id = "j2"


def test_non_retryable_hierarchy():
    assert issubclass(SchemaViolation, NonRetryableError)
    assert issubclass(TenantMismatch, NonRetryableError)
    assert issubclass(UnknownJobType, NonRetryableError)


def test_job_state_values():
    assert JobState.PENDING.value == "PENDING"
    assert JobState.DEAD_LETTERED.value == "DEAD_LETTERED"


# --- Handler tests ---


def test_extraction_handler_is_registered():
    from app.background_jobs import init_handlers
    init_handlers()
    assert is_registered("extraction")
    reg = get_handler("extraction")
    assert reg.max_attempts == 5


def test_extraction_job_schema_validates():
    from app.background_jobs.handlers import ExtractionJob

    job = ExtractionJob(deal_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()))
    assert job.deal_id
    assert job.tenant_id

    with pytest.raises(Exception):
        ExtractionJob(deal_id="abc")


# --- Queue-level PII scrubbing tests ---


def test_mark_failed_scrubs_pii_from_last_error():
    """Spec: last_error containing an OpenAI API key has the key redacted before persistence."""
    from app.background_jobs.queue import scrub_value

    raw_error = "OpenAI call failed: sk-abc123xyzSomeVeryLongSecretKeyHere timeout"
    scrubbed = scrub_value(raw_error)
    assert "sk-abc123" not in scrubbed
    assert "[REDACTED]" in scrubbed

    raw_error_email = "Failed for user john@example.com on deal"
    scrubbed_email = scrub_value(raw_error_email)
    assert "john@example.com" not in scrubbed_email
    assert "[REDACTED]" in scrubbed_email


def test_mark_failed_scrubs_bearer_tokens():
    from app.background_jobs.queue import scrub_value

    raw = "Auth error Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig in request"
    scrubbed = scrub_value(raw)
    assert "eyJhbGciOiJSUzI1NiJ9" not in scrubbed


# --- Admin CLI import test ---


def test_admin_cli_imports():
    from app.background_jobs.admin import list_dead_letter, retry_job, drop_job
    assert callable(list_dead_letter)
    assert callable(retry_job)
    assert callable(drop_job)


# --- Worker entry point test ---


def test_worker_entry_point_module_exists():
    import importlib
    mod = importlib.import_module("app.background_jobs.__main__")
    assert hasattr(mod, "main")


# --- Queue behavior tests ---


@pytest.mark.asyncio
async def test_enqueue_rejects_unregistered_job_type():
    from app.background_jobs import queue

    db = AsyncMock()

    with pytest.raises(UnknownJobType):
        await queue.enqueue(db, job_type="missing", payload={})


@pytest.mark.asyncio
async def test_enqueue_rejects_schema_violation():
    from app.background_jobs import queue

    db = AsyncMock()
    key = f"schema_bad_{uuid.uuid4().hex[:8]}"

    @register(key, schema=_TestPayload, max_attempts=2)
    async def _handler(job, ctx):
        return None

    try:
        with pytest.raises(SchemaViolation):
            await queue.enqueue(db, job_type=key, payload={"deal_id": "only-one-field"})
    finally:
        _registry.pop(key, None)


@pytest.mark.asyncio
async def test_enqueue_returns_existing_job_id_on_idempotency_conflict(monkeypatch):
    from app.background_jobs import queue

    key = f"idempotent_{uuid.uuid4().hex[:8]}"

    @register(key, schema=_TestPayload, max_attempts=2)
    async def _handler(job, ctx):
        return None

    db = _FakeSession(
        execute_results=[
            _Result(rowcount=0),
            _Result(scalar="existing-job-123"),
        ]
    )

    try:
        job_id = await queue.enqueue(
            db,
            job_type=key,
            payload={"deal_id": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4())},
            idempotency_key="same-key",
        )
    finally:
        _registry.pop(key, None)

    assert job_id == "existing-job-123"


@pytest.mark.asyncio
async def test_claim_marks_job_claimed_and_sets_expiry():
    from app.background_jobs import queue

    job = SimpleNamespace(
        job_id="job-1",
        job_type="extraction",
        state=JobState.PENDING.value,
        claimed_by=None,
        claim_expires_at=None,
    )
    db = _FakeSession(execute_results=[_Result(scalar=job)])

    claimed = await queue.claim(db, "worker-1", claim_ttl_seconds=30)

    assert claimed is job
    assert job.state == JobState.CLAIMED.value
    assert job.claimed_by == "worker-1"
    assert job.claim_expires_at is not None
    assert job.claim_expires_at > datetime.now(timezone.utc)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_failed_retries_with_scrubbed_error_and_backoff(monkeypatch):
    from app.background_jobs import queue

    monkeypatch.setattr(queue, "compute_backoff_seconds", lambda attempt: 16.0)
    job = SimpleNamespace(
        job_id="job-2",
        job_type="extraction",
        attempts=0,
        max_attempts=5,
        state=JobState.RUNNING.value,
        last_error=None,
        claimed_by="worker-a",
        claim_expires_at=datetime.now(timezone.utc),
        not_before=datetime.now(timezone.utc),
        dead_lettered_at=None,
    )
    db = _FakeSession()

    await queue.mark_failed(
        db,
        job,
        "OpenAI key sk-secret123 leaked for john@example.com",
        non_retryable=False,
    )

    assert job.attempts == 1
    assert job.state == JobState.PENDING.value
    assert job.claimed_by is None
    assert job.claim_expires_at is None
    assert "john@example.com" not in job.last_error
    assert "[REDACTED]" in job.last_error
    assert job.not_before > datetime.now(timezone.utc)
    assert job.dead_lettered_at is None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_failed_dead_letters_non_retryable_errors():
    from app.background_jobs import queue

    job = SimpleNamespace(
        job_id="job-3",
        job_type="extraction",
        attempts=0,
        max_attempts=5,
        state=JobState.RUNNING.value,
        last_error=None,
        claimed_by="worker-a",
        claim_expires_at=datetime.now(timezone.utc),
        not_before=datetime.now(timezone.utc),
        dead_lettered_at=None,
    )
    db = _FakeSession()

    await queue.mark_failed(db, job, "bad input", non_retryable=True)

    assert job.attempts == 1
    assert job.state == JobState.DEAD_LETTERED.value
    assert job.dead_lettered_at is not None
    assert job.claimed_by is None
    assert job.claim_expires_at is None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_reap_expired_claims_returns_rowcount():
    from app.background_jobs import queue

    db = _FakeSession(execute_results=[_Result(rowcount=2)])

    count = await queue.reap_expired_claims(db)

    assert count == 2


# --- Worker behavior tests ---


@pytest.mark.asyncio
async def test_worker_execute_job_success_marks_running_then_succeeded(monkeypatch):
    from app.background_jobs import worker

    handler = AsyncMock()
    monkeypatch.setattr(
        worker,
        "get_handler",
        lambda _: HandlerRegistration(handler=handler, schema=_TestPayload, max_attempts=3),
    )
    mark_running = AsyncMock()
    mark_succeeded = AsyncMock()
    mark_failed = AsyncMock()
    monkeypatch.setattr(worker, "mark_running", mark_running)
    monkeypatch.setattr(worker, "mark_succeeded", mark_succeeded)
    monkeypatch.setattr(worker, "mark_failed", mark_failed)

    session = _FakeSession()
    job = SimpleNamespace(
        job_id="job-success",
        job_type="any",
        payload={"deal_id": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4())},
        attempts=1,
        trace_context={"trace_id": "t1"},
        tenant_id="tenant-1",
    )
    instance = worker.Worker(lambda: session)

    await instance._execute_job(session, job)

    mark_running.assert_awaited_once_with(session, job)
    mark_succeeded.assert_awaited_once_with(session, job)
    mark_failed.assert_not_called()
    handler.assert_awaited_once()
    typed_payload, ctx = handler.await_args.args
    assert typed_payload.deal_id == job.payload["deal_id"]
    assert ctx.job_id == "job-success"
    assert ctx.attempt == 2
    assert ctx.trace_context == {"trace_id": "t1"}
    assert ctx.tenant_id == "tenant-1"


@pytest.mark.asyncio
async def test_worker_execute_job_dead_letters_unknown_job_types(monkeypatch):
    from app.background_jobs import worker

    monkeypatch.setattr(worker, "get_handler", MagicMock(side_effect=UnknownJobType("nope")))
    mark_failed = AsyncMock()
    monkeypatch.setattr(worker, "mark_failed", mark_failed)

    session = _FakeSession()
    job = SimpleNamespace(job_type="missing")
    instance = worker.Worker(lambda: session)

    await instance._execute_job(session, job)

    assert mark_failed.await_count == 1
    assert mark_failed.await_args.kwargs["non_retryable"] is True


@pytest.mark.asyncio
async def test_worker_execute_job_dead_letters_schema_mismatch(monkeypatch):
    from app.background_jobs import worker

    async def _handler(job, ctx):
        return None

    monkeypatch.setattr(
        worker,
        "get_handler",
        lambda _: HandlerRegistration(handler=_handler, schema=_TestPayload, max_attempts=3),
    )
    mark_failed = AsyncMock()
    monkeypatch.setattr(worker, "mark_failed", mark_failed)

    session = _FakeSession()
    job = SimpleNamespace(
        job_id="job-schema",
        job_type="registered",
        payload={"deal_id": "missing-tenant-id"},
        attempts=0,
        trace_context=None,
        tenant_id=None,
    )
    instance = worker.Worker(lambda: session)

    await instance._execute_job(session, job)

    assert mark_failed.await_count == 1
    assert "Schema validation failed" in mark_failed.await_args.args[2]
    assert mark_failed.await_args.kwargs["non_retryable"] is True


@pytest.mark.asyncio
async def test_worker_execute_job_retries_retryable_exceptions(monkeypatch):
    from app.background_jobs import worker

    async def _handler(job, ctx):
        raise RuntimeError("transient")

    monkeypatch.setattr(
        worker,
        "get_handler",
        lambda _: HandlerRegistration(handler=_handler, schema=_TestPayload, max_attempts=3),
    )
    monkeypatch.setattr(worker, "mark_running", AsyncMock())
    monkeypatch.setattr(worker, "mark_succeeded", AsyncMock())
    mark_failed = AsyncMock()
    monkeypatch.setattr(worker, "mark_failed", mark_failed)

    session = _FakeSession()
    job = SimpleNamespace(
        job_id="job-retry",
        job_type="registered",
        payload={"deal_id": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4())},
        attempts=0,
        trace_context=None,
        tenant_id=None,
    )
    instance = worker.Worker(lambda: session)

    await instance._execute_job(session, job)

    assert mark_failed.await_count == 1
    assert mark_failed.await_args.args[2] == "transient"
    assert mark_failed.await_args.kwargs["non_retryable"] is False


@pytest.mark.asyncio
async def test_worker_execute_job_dead_letters_non_retryable_exceptions(monkeypatch):
    from app.background_jobs import worker

    async def _handler(job, ctx):
        raise TenantMismatch("wrong tenant")

    monkeypatch.setattr(
        worker,
        "get_handler",
        lambda _: HandlerRegistration(handler=_handler, schema=_TestPayload, max_attempts=3),
    )
    monkeypatch.setattr(worker, "mark_running", AsyncMock())
    mark_failed = AsyncMock()
    monkeypatch.setattr(worker, "mark_failed", mark_failed)

    session = _FakeSession()
    job = SimpleNamespace(
        job_id="job-nonretry",
        job_type="registered",
        payload={"deal_id": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4())},
        attempts=0,
        trace_context=None,
        tenant_id=None,
    )
    instance = worker.Worker(lambda: session)

    await instance._execute_job(session, job)

    assert mark_failed.await_count == 1
    assert mark_failed.await_args.args[2] == "wrong tenant"
    assert mark_failed.await_args.kwargs["non_retryable"] is True


# --- Handler and admin behavior tests ---


@pytest.mark.asyncio
async def test_extraction_handler_rejects_tenant_mismatch(monkeypatch):
    from app.background_jobs.handlers import ExtractionJob, handle_extraction

    class _FakeFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setitem(sys.modules, "app.database", SimpleNamespace(async_session_factory=_FakeFactory()))
    monkeypatch.setitem(
        sys.modules,
        "app.services.extraction_service",
        SimpleNamespace(extract_deal=AsyncMock()),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.services.scoring_service",
        SimpleNamespace(score_deal=AsyncMock()),
    )

    job = ExtractionJob(deal_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()))
    ctx = JobContext(
        job_id="job-ctx",
        job_type="extraction",
        attempt=1,
        tenant_id=str(uuid.uuid4()),
    )

    with pytest.raises(TenantMismatch):
        await handle_extraction(job, ctx)


@pytest.mark.asyncio
async def test_admin_retry_uses_same_payload_and_preserves_dead_letter(capsys, monkeypatch):
    from app.background_jobs import admin

    job = SimpleNamespace(
        job_id="dead-1",
        job_type="extraction",
        payload={"deal_id": "d1", "tenant_id": "t1"},
        trace_context={"trace_id": "trace-1"},
        tenant_id="t1",
        state=JobState.DEAD_LETTERED.value,
    )
    session = _FakeSession(execute_results=[_Result(scalar=job)])
    monkeypatch.setattr(admin, "async_session_factory", _SessionFactory(session))
    monkeypatch.setattr(admin, "is_registered", lambda job_type: True)
    enqueue_mock = AsyncMock(return_value="new-job-99")
    monkeypatch.setattr(admin, "enqueue", enqueue_mock)

    await admin.retry_job("dead-1")

    enqueue_mock.assert_awaited_once_with(
        session,
        job_type="extraction",
        payload={"deal_id": "d1", "tenant_id": "t1"},
        trace_context={"trace_id": "trace-1"},
        tenant_id="t1",
    )
    assert "new job new-job-99" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_admin_drop_deletes_only_dead_lettered_job(capsys, monkeypatch):
    from app.background_jobs import admin

    job = SimpleNamespace(job_id="dead-2", state=JobState.DEAD_LETTERED.value)
    session = _FakeSession(execute_results=[_Result(scalar=job)])
    monkeypatch.setattr(admin, "async_session_factory", _SessionFactory(session))

    await admin.drop_job("dead-2")

    session.delete.assert_awaited_once_with(job)
    assert "Dropped dead-lettered job dead-2" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_admin_list_dead_letter_prints_empty_message(capsys, monkeypatch):
    from app.background_jobs import admin

    session = _FakeSession(execute_results=[_Result(scalars=[])])
    monkeypatch.setattr(admin, "async_session_factory", _SessionFactory(session))

    await admin.list_dead_letter()

    assert capsys.readouterr().out.strip() == "No dead-lettered jobs found."


# --- Spec compliance checks ---


def test_spec_documented_worker_cli_module_is_importable():
    """Worker CLI is invokable via `python -m app.background_jobs`."""
    assert importlib.util.find_spec("app.background_jobs.__main__") is not None

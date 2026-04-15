from __future__ import annotations

import enum
from dataclasses import dataclass, field


class JobState(str, enum.Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD_LETTERED = "DEAD_LETTERED"


TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.DEAD_LETTERED})


@dataclass(frozen=True)
class JobContext:
    job_id: str
    job_type: str
    attempt: int
    trace_context: dict | None = field(default=None)
    tenant_id: str | None = field(default=None)


class NonRetryableError(Exception):
    pass


class SchemaViolation(NonRetryableError):
    pass


class TenantMismatch(NonRetryableError):
    pass


class UnknownJobType(NonRetryableError):
    pass


class PermanentAuthFailure(NonRetryableError):
    pass


NON_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    SchemaViolation,
    TenantMismatch,
    UnknownJobType,
    PermanentAuthFailure,
)

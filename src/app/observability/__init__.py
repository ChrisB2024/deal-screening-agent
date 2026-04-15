"""Observability module — Build Order #2.

Structured logging, PII scrubbing, audit log, health endpoints.
Depends on Secrets & Config for log level and exporter credentials.
"""

from .audit import record as audit_record
from .health import router as health_router
from .logger import (
    StructuredLogger,
    get_logger,
    request_id_var,
    tenant_id_var,
    trace_id_var,
    user_id_var,
)
from .middleware import ObservabilityMiddleware

__all__ = [
    "audit_record",
    "get_logger",
    "health_router",
    "ObservabilityMiddleware",
    "StructuredLogger",
    "request_id_var",
    "tenant_id_var",
    "trace_id_var",
    "user_id_var",
]

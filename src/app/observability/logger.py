"""Structured JSON logger factory.

Every log line conforms to the canonical schema from the observability spec.
Context fields (request_id, tenant_id, user_id, module, trace_id) are injected
by middleware — callers never set them directly.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Any

from .scrubber import scrub_fields, scrub_value

request_id_var: ContextVar[str] = ContextVar("request_id", default="none")
tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="none")
user_id_var: ContextVar[str] = ContextVar("user_id", default="none")
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="none")
span_id_var: ContextVar[str] = ContextVar("span_id", default="none")

_PROTECTED_KEYS = frozenset({
    "request_id", "tenant_id", "user_id", "trace_id", "span_id",
    "module", "ts", "level", "event", "duration_ms",
})


class StructuredFormatter(logging.Formatter):
    """Emits one JSON object per log line with the canonical schema."""

    def __init__(self, module_name: str) -> None:
        super().__init__()
        self._module = module_name

    def format(self, record: logging.LogRecord) -> str:
        caller_fields = getattr(record, "_fields", {})
        caller_fields = {k: v for k, v in caller_fields.items() if k not in _PROTECTED_KEYS}
        caller_fields = scrub_fields(caller_fields)

        event = getattr(record, "_event", record.getMessage())
        event = scrub_value(str(event))

        entry: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "event": event,
            "request_id": request_id_var.get(),
            "trace_id": trace_id_var.get(),
            "span_id": span_id_var.get(),
            "tenant_id": tenant_id_var.get(),
            "user_id": user_id_var.get(),
            "module": self._module,
            "duration_ms": getattr(record, "_duration_ms", None),
        }
        if caller_fields:
            entry["fields"] = caller_fields

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            entry["exception"] = record.exc_text

        return json.dumps(entry, default=str)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}Z"


class StructuredLogger:
    """Wrapper around stdlib logger that enforces the canonical schema."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _log(self, level: int, event: str, **fields: Any) -> None:
        record = self._logger.makeRecord(
            name=self._logger.name,
            level=level,
            fn="",
            lno=0,
            msg=event,
            args=(),
            exc_info=None,
        )
        record._event = event  # type: ignore[attr-defined]
        if "duration_ms" in fields:
            record._duration_ms = fields.pop("duration_ms")  # type: ignore[attr-defined]
        record._fields = fields  # type: ignore[attr-defined]
        self._logger.handle(record)

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, **fields)

    def critical(self, event: str, **fields: Any) -> None:
        self._log(logging.CRITICAL, event, **fields)


_loggers: dict[str, StructuredLogger] = {}


def get_logger(module_name: str) -> StructuredLogger:
    if module_name in _loggers:
        return _loggers[module_name]

    stdlib_logger = logging.getLogger(f"app.{module_name}")
    stdlib_logger.setLevel(logging.DEBUG)
    stdlib_logger.propagate = False

    if not stdlib_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter(module_name))
        stdlib_logger.addHandler(handler)

    wrapped = StructuredLogger(stdlib_logger)
    _loggers[module_name] = wrapped
    return wrapped

"""Log-level PII and secret scrubber.

Runs synchronously on every log record before emit. Complements the
document-level pii_scrubber.py (which handles deal text before LLM calls);
this one handles operational log fields.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[REDACTED]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[REDACTED]"),
    (re.compile(r"Bearer\s+\S+", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "[REDACTED]"),
]

_BLOCKED_KEYS = frozenset({
    "password", "token", "secret", "authorization", "cookie",
    "api_key", "apikey", "access_token", "refresh_token",
    "signing_key", "private_key",
})


def scrub_value(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def scrub_fields(fields: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for k, v in fields.items():
        if k.lower() in _BLOCKED_KEYS:
            result[k] = "[REDACTED]"
        elif isinstance(v, str):
            result[k] = scrub_value(v)
        elif isinstance(v, dict):
            result[k] = scrub_fields(v)
        else:
            result[k] = v
    return result

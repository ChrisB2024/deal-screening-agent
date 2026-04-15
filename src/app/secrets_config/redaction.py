from __future__ import annotations

import hashlib
import re
import threading
from typing import Any


class RedactionRegistry:
    """Append-only registry of secret values that must be scrubbed from logs.

    Thread-safe. Values are stored as SHA-256 hashes for lookup; the original
    value is kept only for the replacement scan (unavoidable — you need the
    plaintext to find it in a string). Old rotated-out values are never removed
    so in-flight log records still get scrubbed.
    """

    PLACEHOLDER = "[REDACTED]"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secret_names: set[str] = set()
        self._known_values: list[str] = []
        self._value_hashes: set[str] = set()

    def register_key(self, name: str) -> None:
        with self._lock:
            self._secret_names.add(name)

    def register_value(self, value: str) -> None:
        h = hashlib.sha256(value.encode()).hexdigest()
        with self._lock:
            if h not in self._value_hashes:
                self._value_hashes.add(h)
                self._known_values.append(value)

    def redact(self, text: str) -> str:
        with self._lock:
            values = list(self._known_values)
        for v in values:
            if v and v in text:
                text = text.replace(v, self.PLACEHOLDER)
        return text

    def redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact secret values from a dict (for structured logs)."""
        result = {}
        with self._lock:
            blocked_keys = set(self._secret_names)
        for k, v in data.items():
            if k.lower() in blocked_keys or k.lower() in (
                "password", "token", "secret", "authorization", "cookie", "api_key",
            ):
                result[k] = self.PLACEHOLDER
            elif isinstance(v, str):
                result[k] = self.redact(v)
            elif isinstance(v, dict):
                result[k] = self.redact_dict(v)
            else:
                result[k] = v
        return result

    @property
    def registered_count(self) -> int:
        with self._lock:
            return len(self._known_values)

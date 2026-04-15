from __future__ import annotations

import random

BASE_DELAYS = [1, 4, 16, 64, 256]


def compute_backoff_seconds(attempt: int, *, jitter_fraction: float = 0.25) -> float:
    idx = min(attempt, len(BASE_DELAYS) - 1)
    base = BASE_DELAYS[idx]
    jitter = base * jitter_fraction * (2 * random.random() - 1)
    return max(0.0, base + jitter)

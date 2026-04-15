"""Rate limit backing store — abstract interface + in-memory LRU implementation.

Portfolio phase uses InMemoryStore (single process). Production swaps in
RedisStore (cross-instance consistency) behind the same interface.
"""

from __future__ import annotations

import abc
import threading
import time
from collections import OrderedDict

from .bucket import BucketConfig, BucketState, CheckResult, check_and_consume, new_bucket


class RateLimitStore(abc.ABC):
    """Abstract backing store for rate limit buckets."""

    @abc.abstractmethod
    async def check(self, key: str, config: BucketConfig, cost: int = 1) -> CheckResult:
        """Atomically refill + check + consume for the given key."""

    @abc.abstractmethod
    async def reset(self, key: str) -> None:
        """Reset a bucket to full (admin action)."""

    @abc.abstractmethod
    async def get_state(self, key: str) -> BucketState | None:
        """Read current bucket state without consuming (debug/admin)."""


class StoreUnavailableError(Exception):
    pass


class InMemoryStore(RateLimitStore):
    """Thread-safe in-memory LRU store. Suitable for single-process deployments."""

    def __init__(self, max_keys: int = 100_000) -> None:
        self._buckets: OrderedDict[str, tuple[BucketState, BucketConfig]] = OrderedDict()
        self._max_keys = max_keys
        self._lock = threading.Lock()

    async def check(self, key: str, config: BucketConfig, cost: int = 1) -> CheckResult:
        now = time.time()
        with self._lock:
            entry = self._buckets.get(key)
            if entry is None:
                state = new_bucket(config, now)
            else:
                state = entry[0]

            result, new_state = check_and_consume(state, config, cost, now)
            self._buckets[key] = (new_state, config)
            self._buckets.move_to_end(key)

            self._evict_expired(now)

            return result

    async def reset(self, key: str) -> None:
        with self._lock:
            entry = self._buckets.get(key)
            if entry is not None:
                config = entry[1]
                self._buckets[key] = (new_bucket(config), config)

    async def get_state(self, key: str) -> BucketState | None:
        with self._lock:
            entry = self._buckets.get(key)
            return entry[0] if entry else None

    def _evict_expired(self, now: float) -> None:
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)

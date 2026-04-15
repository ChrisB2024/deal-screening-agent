"""Token bucket algorithm for rate limiting.

Each bucket tracks tokens_remaining and last_refill_at. On each check:
refill based on elapsed time, then check if tokens >= cost.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class BucketState:
    tokens: float
    last_refill_at: float


@dataclass(frozen=True)
class BucketConfig:
    burst: int
    refill_rate: float  # tokens per second

    @classmethod
    def per_minute(cls, burst: int, rpm: int) -> BucketConfig:
        return cls(burst=burst, refill_rate=rpm / 60.0)

    @classmethod
    def per_hour(cls, burst: int, rph: int) -> BucketConfig:
        return cls(burst=burst, refill_rate=rph / 3600.0)

    @property
    def full_refill_seconds(self) -> float:
        if self.refill_rate <= 0:
            return 3600.0
        return self.burst / self.refill_rate


@dataclass(frozen=True)
class CheckResult:
    allowed: bool
    tokens_remaining: float
    limit: int
    retry_after_seconds: int  # 0 if allowed
    reset_at: float  # unix timestamp when bucket is full


def refill(state: BucketState, config: BucketConfig, now: float | None = None) -> BucketState:
    """Refill tokens based on elapsed time. Monotonic: last_refill_at only moves forward."""
    if now is None:
        now = time.time()
    effective_now = max(now, state.last_refill_at)
    elapsed = effective_now - state.last_refill_at
    new_tokens = min(config.burst, state.tokens + elapsed * config.refill_rate)
    return BucketState(tokens=new_tokens, last_refill_at=effective_now)


def check_and_consume(
    state: BucketState, config: BucketConfig, cost: int = 1, now: float | None = None
) -> tuple[CheckResult, BucketState]:
    """Refill, then try to consume. Returns (result, new_state)."""
    if now is None:
        now = time.time()
    state = refill(state, config, now)

    reset_at = now + (config.burst - state.tokens) / config.refill_rate if config.refill_rate > 0 else now

    if state.tokens >= cost:
        new_state = BucketState(tokens=state.tokens - cost, last_refill_at=state.last_refill_at)
        return (
            CheckResult(
                allowed=True,
                tokens_remaining=new_state.tokens,
                limit=config.burst,
                retry_after_seconds=0,
                reset_at=reset_at,
            ),
            new_state,
        )

    deficit = cost - state.tokens
    retry_after = deficit / config.refill_rate if config.refill_rate > 0 else 3600
    return (
        CheckResult(
            allowed=False,
            tokens_remaining=state.tokens,
            limit=config.burst,
            retry_after_seconds=max(1, int(retry_after + 0.999)),
            reset_at=reset_at,
        ),
        state,  # no decrement on deny
    )


def new_bucket(config: BucketConfig, now: float | None = None) -> BucketState:
    """Create a full bucket."""
    if now is None:
        now = time.time()
    return BucketState(tokens=float(config.burst), last_refill_at=now)

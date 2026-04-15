"""Rate Limiter module — token bucket rate limiting with layered scopes.

Public API:
- RateLimitMiddleware: FastAPI middleware (add after ObservabilityMiddleware)
- init_rate_limiter: configure store, limits, trusted proxies at startup
- RateLimitStore / InMemoryStore: backing store interface + portfolio impl
- LimitsTable: lookup table for scope/group → bucket config
"""

from .config import EndpointGroup, LimitsTable, Scope
from .middleware import RateLimitMiddleware, init_rate_limiter
from .store import InMemoryStore, RateLimitStore

__all__ = [
    "EndpointGroup",
    "InMemoryStore",
    "LimitsTable",
    "RateLimitMiddleware",
    "RateLimitStore",
    "Scope",
    "init_rate_limiter",
]

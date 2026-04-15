"""Rate limit configuration — endpoint groups, scope definitions, default limits.

Limits are loaded from secrets_config at startup. This module defines the
structure and defaults that get merged with runtime config.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from .bucket import BucketConfig


class EndpointGroup(str, enum.Enum):
    AUTH_LOGIN = "auth_login"
    AUTH_REFRESH = "auth_refresh"
    UPLOAD = "upload"
    RESCORE = "rescore"
    DEFAULT = "default"


class Scope(str, enum.Enum):
    IP = "ip"
    EMAIL = "email"
    USER = "user"
    TENANT = "tenant"


@dataclass(frozen=True)
class ScopeLimit:
    scope: Scope
    group: EndpointGroup
    config: BucketConfig


DEFAULT_LIMITS: list[ScopeLimit] = [
    # Auth endpoints — strictest
    ScopeLimit(Scope.IP, EndpointGroup.AUTH_LOGIN, BucketConfig.per_minute(5, 5)),
    ScopeLimit(Scope.EMAIL, EndpointGroup.AUTH_LOGIN, BucketConfig.per_hour(20, 20)),
    ScopeLimit(Scope.IP, EndpointGroup.AUTH_REFRESH, BucketConfig.per_minute(10, 10)),
    # Upload — expensive
    ScopeLimit(Scope.USER, EndpointGroup.UPLOAD, BucketConfig.per_minute(10, 10)),
    ScopeLimit(Scope.TENANT, EndpointGroup.UPLOAD, BucketConfig.per_minute(100, 100)),
    # Rescore — expensive
    ScopeLimit(Scope.USER, EndpointGroup.RESCORE, BucketConfig.per_minute(20, 20)),
    # General
    ScopeLimit(Scope.IP, EndpointGroup.DEFAULT, BucketConfig.per_minute(60, 60)),
    ScopeLimit(Scope.USER, EndpointGroup.DEFAULT, BucketConfig.per_minute(120, 120)),
    ScopeLimit(Scope.TENANT, EndpointGroup.DEFAULT, BucketConfig.per_minute(600, 600)),
]

ROUTE_GROUP_MAP: dict[str, EndpointGroup] = {
    "/api/v1/auth/login": EndpointGroup.AUTH_LOGIN,
    "/api/v1/auth/refresh": EndpointGroup.AUTH_REFRESH,
    "/api/v1/deals/upload": EndpointGroup.UPLOAD,
}


def resolve_endpoint_group(path: str) -> EndpointGroup:
    """Map a request path to its endpoint group."""
    for prefix, group in ROUTE_GROUP_MAP.items():
        if path == prefix or path.startswith(prefix + "/"):
            return group
    return EndpointGroup.DEFAULT


@dataclass
class LimitsTable:
    """Lookup table: (scope, group) → BucketConfig."""

    _table: dict[tuple[Scope, EndpointGroup], BucketConfig] = field(default_factory=dict)

    @classmethod
    def from_defaults(cls) -> LimitsTable:
        table = cls()
        for sl in DEFAULT_LIMITS:
            table._table[(sl.scope, sl.group)] = sl.config
        return table

    def get(self, scope: Scope, group: EndpointGroup) -> BucketConfig | None:
        return self._table.get((scope, group))

    def set(self, scope: Scope, group: EndpointGroup, config: BucketConfig) -> None:
        self._table[(scope, group)] = config

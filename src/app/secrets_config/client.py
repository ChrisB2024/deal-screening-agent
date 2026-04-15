from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .providers import SecretsProviderProtocol
from .redaction import RedactionRegistry
from .types import CANONICAL_SECRET_NAMES, SecretValue, UnknownSecretError

logger = logging.getLogger(__name__)


@dataclass
class SubscriptionHandle:
    name: str
    callback: Callable[[SecretValue], Awaitable[None]]
    id: int = field(default_factory=lambda: id(object()))


class SecretsClient:
    """Central access point for all secrets. Singleton created by bootstrap()."""

    def __init__(
        self,
        provider: SecretsProviderProtocol,
        redaction_registry: RedactionRegistry,
        refresh_interval_s: float = 60.0,
        callback_timeout_s: float = 5.0,
    ) -> None:
        self._provider = provider
        self._registry = redaction_registry
        self._refresh_interval_s = refresh_interval_s
        self._callback_timeout_s = callback_timeout_s
        self._secrets: dict[str, SecretValue] = {}
        self._subscriptions: dict[str, list[SubscriptionHandle]] = {}
        self._refresher_task: asyncio.Task[Any] | None = None

    def load_initial(self) -> list[str]:
        """Load all secrets from the provider. Returns list of loaded names."""
        self._secrets = self._provider.load_all()
        for sv in self._secrets.values():
            self._registry.register_key(sv.name)
            self._registry.register_value(sv.reveal_str())
        return list(self._secrets.keys())

    def get(self, name: str) -> SecretValue:
        if name not in CANONICAL_SECRET_NAMES:
            raise UnknownSecretError(f"Unknown secret: {name!r}")
        sv = self._secrets.get(name)
        if sv is None:
            raise UnknownSecretError(f"Secret not loaded: {name!r}")
        return sv

    def get_optional(self, name: str) -> SecretValue | None:
        if name not in CANONICAL_SECRET_NAMES:
            raise UnknownSecretError(f"Unknown secret: {name!r}")
        return self._secrets.get(name)

    def subscribe(
        self, name: str, callback: Callable[[SecretValue], Awaitable[None]]
    ) -> SubscriptionHandle:
        if name not in CANONICAL_SECRET_NAMES:
            raise UnknownSecretError(f"Unknown secret: {name!r}")
        handle = SubscriptionHandle(name=name, callback=callback)
        self._subscriptions.setdefault(name, []).append(handle)
        return handle

    async def start_refresher(self) -> None:
        if self._refresher_task is not None:
            return
        self._refresher_task = asyncio.create_task(self._refresh_loop())

    async def stop_refresher(self) -> None:
        if self._refresher_task is None:
            return
        self._refresher_task.cancel()
        try:
            await self._refresher_task
        except asyncio.CancelledError:
            pass
        self._refresher_task = None

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval_s)
            for name in list(self._secrets.keys()):
                try:
                    new_sv = self._provider.fetch(name)
                except Exception:
                    logger.warning("refresh_fetch_failed", extra={"secret_name": name})
                    continue
                if new_sv is None:
                    continue
                old_sv = self._secrets.get(name)
                if old_sv and old_sv.version == new_sv.version:
                    continue
                self._secrets[name] = new_sv
                self._registry.register_value(new_sv.reveal_str())
                await self._notify_subscribers(name, new_sv)

    async def _notify_subscribers(self, name: str, new_sv: SecretValue) -> None:
        for handle in self._subscriptions.get(name, []):
            try:
                await asyncio.wait_for(
                    handle.callback(new_sv),
                    timeout=self._callback_timeout_s,
                )
            except asyncio.TimeoutError:
                logger.error("subscriber_callback_timeout", extra={
                    "secret_name": name, "subscription_id": handle.id,
                })
            except Exception:
                logger.error("subscriber_callback_failed", extra={
                    "secret_name": name, "subscription_id": handle.id,
                }, exc_info=True)

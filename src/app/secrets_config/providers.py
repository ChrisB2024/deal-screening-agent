from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .types import CANONICAL_SECRET_NAMES, SecretValue, UnknownSecretError


class SecretsProviderProtocol(Protocol):
    """Interface that all secret providers implement."""

    def load_all(self) -> dict[str, SecretValue]:
        """Load all known secrets. Called once at bootstrap."""
        ...

    def fetch(self, name: str) -> SecretValue | None:
        """Fetch a single secret by name. Used by the refresher loop."""
        ...


# --- .env file provider (dev only) ---


ENV_FILE_KEY_MAP = {
    "openai_api_key": "OPENAI_API_KEY",
    "database_password": "DATABASE_PASSWORD",
    "auth_signing_key_current": "AUTH_SIGNING_KEY_CURRENT",
    "auth_signing_key_previous": "AUTH_SIGNING_KEY_PREVIOUS",
    "rate_limit_store_password": "RATE_LIMIT_STORE_PASSWORD",
    "auth_min_iat_watermark": "AUTH_MIN_IAT_WATERMARK",
}


class EnvFileProvider:
    """Reads secrets from a .env file on disk. Dev environment only."""

    def __init__(self, env_path: Path | None = None) -> None:
        self._path = env_path or Path(".env")
        self._values: dict[str, str] = {}
        self._load_file()

    def _load_file(self) -> None:
        if not self._path.exists():
            return
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            self._values[key.strip()] = value.strip()

    def load_all(self) -> dict[str, SecretValue]:
        now = datetime.now(timezone.utc)
        result: dict[str, SecretValue] = {}
        for secret_name in CANONICAL_SECRET_NAMES:
            env_key = ENV_FILE_KEY_MAP.get(secret_name, secret_name.upper())
            raw = self._values.get(env_key, "")
            if raw:
                result[secret_name] = SecretValue(
                    name=secret_name,
                    version="env-v1",
                    loaded_at=now,
                    _value=raw.encode("utf-8"),
                )
        return result

    def fetch(self, name: str) -> SecretValue | None:
        if name not in CANONICAL_SECRET_NAMES:
            raise UnknownSecretError(name)
        self._load_file()
        env_key = ENV_FILE_KEY_MAP.get(name, name.upper())
        raw = self._values.get(env_key, "")
        if not raw:
            return None
        return SecretValue(
            name=name,
            version="env-v1",
            loaded_at=datetime.now(timezone.utc),
            _value=raw.encode("utf-8"),
        )


# --- In-memory provider (test only) ---


class MemoryProvider:
    """In-process dict-backed provider for tests. Seed via constructor or set()."""

    def __init__(self, seed: dict[str, str] | None = None) -> None:
        self._store: dict[str, tuple[str, str]] = {}
        if seed:
            for name, value in seed.items():
                self._store[name] = (value, "test-v1")

    def set(self, name: str, value: str, version: str = "test-v1") -> None:
        self._store[name] = (value, version)

    def load_all(self) -> dict[str, SecretValue]:
        now = datetime.now(timezone.utc)
        return {
            name: SecretValue(name=name, version=ver, loaded_at=now, _value=val.encode("utf-8"))
            for name, (val, ver) in self._store.items()
        }

    def fetch(self, name: str) -> SecretValue | None:
        if name not in self._store:
            return None
        val, ver = self._store[name]
        return SecretValue(
            name=name,
            version=ver,
            loaded_at=datetime.now(timezone.utc),
            _value=val.encode("utf-8"),
        )


# --- AWS Secrets Manager provider (prod) ---
# TODO: Implement AwsSecretsManagerProvider
#   - Use boto3 to load secrets by name prefix
#   - Support native rotation detection via version metadata
#   - IAM role scoped to secretsmanager:GetSecretValue on prefix/*
#   - Lazy-import boto3 so it's not required in dev/test


# --- Fly.io secrets provider ---
# TODO: Implement FlyioSecretsProvider
#   - Read from runtime environment variables (Fly.io injects secrets as env vars)
#   - No native rotation; refresher is a no-op
#   - Valid for prod (portfolio) and staging

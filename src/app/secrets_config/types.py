from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from ipaddress import IPv4Network, IPv6Network
from typing import Literal

from pydantic import BaseModel, model_validator


class Environment(str, enum.Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"
    TEST = "test"


class SecretsProvider(str, enum.Enum):
    AWS_SECRETS_MANAGER = "aws_secrets_manager"
    FLYIO_SECRETS = "flyio_secrets"
    ENV_FILE = "env_file"
    MEMORY = "memory"


class KMSProviderType(str, enum.Enum):
    AWS_KMS = "aws_kms"
    LOCAL_AES = "local_aes"
    NONE = "none"


PROVIDER_ENV_MATRIX: dict[Environment, set[SecretsProvider]] = {
    Environment.PROD: {SecretsProvider.AWS_SECRETS_MANAGER, SecretsProvider.FLYIO_SECRETS},
    Environment.STAGING: {SecretsProvider.AWS_SECRETS_MANAGER, SecretsProvider.FLYIO_SECRETS},
    Environment.DEV: {
        SecretsProvider.AWS_SECRETS_MANAGER,
        SecretsProvider.FLYIO_SECRETS,
        SecretsProvider.ENV_FILE,
    },
    Environment.TEST: {SecretsProvider.ENV_FILE, SecretsProvider.MEMORY},
}

CANONICAL_SECRET_NAMES = frozenset(
    {
        "openai_api_key",
        "database_password",
        "auth_signing_key_current",
        "auth_signing_key_previous",
        "rate_limit_store_password",
        "auth_min_iat_watermark",
    }
)


# --- Errors ---


class BootstrapError(Exception):
    pass


class BootstrapNotRunError(RuntimeError):
    pass


class UnknownSecretError(KeyError):
    pass


class DecryptError(Exception):
    pass


# --- SecretValue ---


@dataclass(frozen=True)
class SecretValue:
    name: str
    version: str
    _value: bytes = field(repr=False)
    loaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def reveal(self) -> bytes:
        return self._value

    def reveal_str(self) -> str:
        return self._value.decode("utf-8")

    def __repr__(self) -> str:
        return f"SecretValue(name={self.name!r}, version={self.version!r})"

    def __str__(self) -> str:
        return self.__repr__()


# --- Config sub-schemas ---


class DatabaseConfig(BaseModel, frozen=True):
    host: str = "localhost"
    port: int = 5432
    name: str = "deal_screening"
    driver: str = "postgresql+asyncpg"
    pool_size: int = 10
    max_overflow: int = 20
    ssl_mode: str = "prefer"

    @property
    def url_without_password(self) -> str:
        return f"{self.driver}://{self.host}:{self.port}/{self.name}"


class StorageConfig(BaseModel, frozen=True):
    backend: Literal["local", "s3"] = "local"
    upload_dir: str = "./uploads"
    bucket: str = ""
    region: str = ""
    prefix: str = ""
    max_file_size_bytes: int = 50 * 1024 * 1024


class LLMConfig(BaseModel, frozen=True):
    provider: str = "openai"
    model: str = "gpt-4o"
    max_tokens: int = 4096
    timeout_s: int = 30


class Argon2Params(BaseModel, frozen=True):
    memory_kib: int = 65536
    iterations: int = 3
    parallelism: int = 1

    @model_validator(mode="after")
    def _enforce_floor(self):
        if self.memory_kib < 65536 or self.iterations < 3 or self.parallelism < 1:
            raise ValueError("argon2 params below OWASP floor")
        return self


class AuthConfig(BaseModel, frozen=True):
    access_ttl_s: int = 900
    refresh_ttl_s: int = 604800
    issuer: str = "deal-screening-agent"
    argon2: Argon2Params = Argon2Params()
    signing_key_overlap_days: int = 7
    signing_key_rotation_days: int = 90


class RateLimitStoreConfig(BaseModel, frozen=True):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    tls: bool = False
    timeout_ms: int = 50


class RateLimitConfig(BaseModel, frozen=True):
    requests_per_minute: int = 100
    store: RateLimitStoreConfig = RateLimitStoreConfig()
    trusted_proxy_cidrs: list[IPv4Network | IPv6Network] = []


class ObsConfig(BaseModel, frozen=True):
    otlp_endpoint: str = ""
    sample_rate: float = 1.0
    service_name: str = "deal-screening-agent"


# --- AppConfig ---


class AppConfig(BaseModel, frozen=True):
    env: Environment = Environment.DEV
    app_version: str = "0.1.0"
    log_level: Literal["DEBUG", "INFO", "WARN", "ERROR"] = "INFO"

    database: DatabaseConfig = DatabaseConfig()
    storage: StorageConfig = StorageConfig()
    llm: LLMConfig = LLMConfig()
    auth: AuthConfig = AuthConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    observability: ObsConfig = ObsConfig()

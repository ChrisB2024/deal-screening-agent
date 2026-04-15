from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

from .client import SecretsClient
from .kms import KMSClientProtocol, LocalAESClient, NoneKMSClient
from .providers import EnvFileProvider, MemoryProvider, SecretsProviderProtocol
from .redaction import RedactionRegistry
from .types import (
    AppConfig,
    BootstrapError,
    Environment,
    KMSProviderType,
    PROVIDER_ENV_MATRIX,
    SecretsProvider,
)

logger = logging.getLogger(__name__)

_config: AppConfig | None = None
_secrets_client: SecretsClient | None = None
_kms_client: KMSClientProtocol | None = None
_redaction_registry: RedactionRegistry | None = None
_bootstrapped = False


def _write_audit_entry(event: str, env: str, provider: str, names: list[str]) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "env": env,
        "provider": provider,
        "secret_names": names,
        "request_id": None,
    }
    print(json.dumps(entry), file=sys.stderr, flush=True)


def _resolve_provider(env: Environment, provider_name: SecretsProvider) -> SecretsProviderProtocol:
    valid = PROVIDER_ENV_MATRIX[env]
    if provider_name not in valid:
        raise BootstrapError(
            f"Provider {provider_name.value!r} is not valid for env {env.value!r}. "
            f"Valid: {[p.value for p in valid]}"
        )
    if provider_name == SecretsProvider.ENV_FILE:
        return EnvFileProvider()
    if provider_name == SecretsProvider.MEMORY:
        return MemoryProvider()
    # TODO: AWS and Fly.io providers
    raise BootstrapError(f"Provider {provider_name.value!r} is not yet implemented")


def _resolve_kms(kms_type: KMSProviderType) -> KMSClientProtocol:
    if kms_type == KMSProviderType.NONE:
        return NoneKMSClient()
    if kms_type == KMSProviderType.LOCAL_AES:
        return LocalAESClient()
    # TODO: AWS KMS
    raise BootstrapError(f"KMS provider {kms_type.value!r} is not yet implemented")


def _load_app_config() -> AppConfig:
    """Build AppConfig from environment variables.

    Non-secret config is loaded here. Secrets come from SecretsClient.
    """
    env_str = os.environ.get("APP_ENV", "dev")
    try:
        env = Environment(env_str)
    except ValueError:
        raise BootstrapError(f"Invalid APP_ENV: {env_str!r}")

    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = int(os.environ.get("DB_PORT", "5432"))
    db_name = os.environ.get("DB_NAME", "deal_screening")
    db_driver = os.environ.get("DB_DRIVER", "postgresql+asyncpg")

    return AppConfig(
        env=env,
        app_version=os.environ.get("APP_VERSION", "0.1.0"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        database=AppConfig.model_fields["database"].default.__class__(
            host=db_host,
            port=db_port,
            name=db_name,
            driver=db_driver,
            pool_size=int(os.environ.get("DB_POOL_SIZE", "10")),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "20")),
            ssl_mode=os.environ.get("DB_SSL_MODE", "prefer"),
        ),
        storage=AppConfig.model_fields["storage"].default.__class__(
            backend=os.environ.get("STORAGE_BACKEND", "local"),
            upload_dir=os.environ.get("UPLOAD_DIR", "./uploads"),
            max_file_size_bytes=int(os.environ.get("MAX_FILE_SIZE_BYTES", str(50 * 1024 * 1024))),
        ),
        llm=AppConfig.model_fields["llm"].default.__class__(
            provider=os.environ.get("LLM_PROVIDER", "openai"),
            model=os.environ.get("LLM_MODEL", "gpt-4o"),
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "4096")),
            timeout_s=int(os.environ.get("LLM_TIMEOUT_S", "30")),
        ),
    )


async def bootstrap(
    provider_override: SecretsProviderProtocol | None = None,
    config_override: AppConfig | None = None,
) -> None:
    """Initialize the secrets & config module. Must be called before any other module.

    Args:
        provider_override: Inject a custom provider (useful for tests).
        config_override: Inject a custom config (useful for tests).
    """
    global _config, _secrets_client, _kms_client, _redaction_registry, _bootstrapped

    if _bootstrapped:
        return

    registry = RedactionRegistry()
    _redaction_registry = registry

    config = config_override or _load_app_config()
    _config = config

    env = config.env
    provider_name_str = os.environ.get("SECRETS_PROVIDER", "")
    kms_type_str = os.environ.get("KMS_PROVIDER", "none")

    if not provider_name_str:
        defaults = {
            Environment.DEV: SecretsProvider.ENV_FILE,
            Environment.TEST: SecretsProvider.MEMORY,
            Environment.STAGING: SecretsProvider.AWS_SECRETS_MANAGER,
            Environment.PROD: SecretsProvider.AWS_SECRETS_MANAGER,
        }
        provider_name = defaults[env]
    else:
        provider_name = SecretsProvider(provider_name_str)

    if provider_override:
        provider = provider_override
    else:
        provider = _resolve_provider(env, provider_name)

    try:
        kms_type = KMSProviderType(kms_type_str)
    except ValueError:
        raise BootstrapError(f"Invalid KMS_PROVIDER: {kms_type_str!r}")
    _kms_client = _resolve_kms(kms_type)

    client = SecretsClient(provider=provider, redaction_registry=registry)
    loaded_names = client.load_initial()
    _secrets_client = client

    _write_audit_entry(
        event="secrets_loaded",
        env=env.value,
        provider=provider_name.value if not provider_override else "override",
        names=loaded_names,
    )

    await client.start_refresher()
    _bootstrapped = True
    logger.info("bootstrap_complete", extra={
        "env": env.value, "secrets_loaded": len(loaded_names),
    })


async def shutdown() -> None:
    global _bootstrapped
    if _secrets_client:
        await _secrets_client.stop_refresher()
    _bootstrapped = False


def reset() -> None:
    """Reset all singletons. Test-only."""
    global _config, _secrets_client, _kms_client, _redaction_registry, _bootstrapped
    _config = None
    _secrets_client = None
    _kms_client = None
    _redaction_registry = None
    _bootstrapped = False

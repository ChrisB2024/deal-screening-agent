"""Secrets & Config module — Build Order #1.

Every other module depends on this. Call bootstrap() before anything else.
"""

from .bootstrap import bootstrap, reset, shutdown
from .bootstrap import _config, _kms_client, _redaction_registry, _secrets_client
from .client import SecretsClient
from .kms import Ciphertext, DataKey, KMSClientProtocol
from .redaction import RedactionRegistry
from .types import (
    AppConfig,
    BootstrapError,
    BootstrapNotRunError,
    DecryptError,
    Environment,
    SecretValue,
    UnknownSecretError,
)


def _bootstrap_module():
    import sys
    return sys.modules[__name__.rsplit(".", 1)[0] + ".secrets_config.bootstrap"]


def get_config() -> AppConfig:
    _b = _bootstrap_module()
    if _b._config is None:
        raise BootstrapNotRunError("bootstrap() has not been called")
    return _b._config


def get_secrets() -> SecretsClient:
    _b = _bootstrap_module()
    if _b._secrets_client is None:
        raise BootstrapNotRunError("bootstrap() has not been called")
    return _b._secrets_client


def get_kms() -> KMSClientProtocol:
    _b = _bootstrap_module()
    if _b._kms_client is None:
        raise BootstrapNotRunError("bootstrap() has not been called")
    return _b._kms_client


def get_redaction_registry() -> RedactionRegistry:
    _b = _bootstrap_module()
    if _b._redaction_registry is None:
        raise BootstrapNotRunError("bootstrap() has not been called")
    return _b._redaction_registry


__all__ = [
    "bootstrap",
    "shutdown",
    "reset",
    "get_config",
    "get_secrets",
    "get_kms",
    "get_redaction_registry",
    "AppConfig",
    "SecretValue",
    "SecretsClient",
    "RedactionRegistry",
    "KMSClientProtocol",
    "Ciphertext",
    "DataKey",
    "Environment",
    "BootstrapError",
    "BootstrapNotRunError",
    "UnknownSecretError",
    "DecryptError",
]

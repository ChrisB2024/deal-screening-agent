"""Argon2id password hashing per OWASP recommendations.

Uniform timing: unknown-email login paths call dummy_verify() to prevent
user enumeration via timing side channels.
"""

from __future__ import annotations

import argon2

_hasher: argon2.PasswordHasher | None = None


def _get_hasher() -> argon2.PasswordHasher:
    global _hasher
    if _hasher is not None:
        return _hasher
    from app.secrets_config import get_config
    params = get_config().auth.argon2
    _hasher = argon2.PasswordHasher(
        time_cost=params.iterations,
        memory_cost=params.memory_kib,
        parallelism=params.parallelism,
    )
    return _hasher


def reset_hasher() -> None:
    global _hasher
    _hasher = None


def hash_password(password: str) -> str:
    return _get_hasher().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _get_hasher().verify(password_hash, password)
    except (argon2.exceptions.VerifyMismatchError, argon2.exceptions.VerificationError):
        return False


def dummy_verify() -> None:
    """Burn the same CPU time as a real verify to prevent timing leaks."""
    _get_hasher().hash("dummy_password_for_uniform_timing")

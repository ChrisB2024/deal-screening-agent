"""JWT access tokens (Ed25519) and opaque refresh tokens.

Access tokens are signed with EdDSA. The KeyRing holds current + previous
key pairs for rotation overlap. Refresh tokens are opaque strings stored
as SHA-256 hashes — the raw token is never persisted.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@dataclass
class SigningKey:
    kid: str
    private_key: Ed25519PrivateKey

    @property
    def public_key(self):
        return self.private_key.public_key()


class KeyRing:
    def __init__(self) -> None:
        self.current: SigningKey | None = None
        self.previous: SigningKey | None = None

    @property
    def all_keys(self) -> list[SigningKey]:
        return [k for k in [self.current, self.previous] if k is not None]


_keyring = KeyRing()


def get_keyring() -> KeyRing:
    return _keyring


def init_signing_keys(
    current_key_bytes: bytes,
    current_kid: str,
    previous_key_bytes: bytes | None = None,
    previous_kid: str | None = None,
) -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(current_key_bytes[:32])
    _keyring.current = SigningKey(kid=current_kid, private_key=private_key)

    if previous_key_bytes and previous_kid:
        prev_key = Ed25519PrivateKey.from_private_bytes(previous_key_bytes[:32])
        _keyring.previous = SigningKey(kid=previous_kid, private_key=prev_key)


def generate_ephemeral_keys() -> None:
    """Generate throwaway Ed25519 keys for dev/test environments."""
    _keyring.current = SigningKey(
        kid="ephemeral_dev",
        private_key=Ed25519PrivateKey.generate(),
    )
    _keyring.previous = None


def reset_keyring() -> None:
    _keyring.current = None
    _keyring.previous = None


def create_access_token(
    user_id: str,
    tenant_id: str,
    session_id: str,
    roles: list[str] | None = None,
    issuer: str = "deal-screening-agent",
    ttl_seconds: int = 900,
) -> str:
    key = _keyring.current
    if key is None:
        raise RuntimeError("Signing keys not initialized")

    now = datetime.now(timezone.utc)
    claims = {
        "iss": issuer,
        "sub": user_id,
        "tnt": tenant_id,
        "sid": session_id,
        "roles": roles or [],
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(
        claims,
        key.private_key,
        algorithm="EdDSA",
        headers={"kid": key.kid},
    )


def verify_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises jwt.InvalidTokenError on any failure."""
    keys = _keyring.all_keys
    if not keys:
        raise RuntimeError("No signing keys available")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.DecodeError as exc:
        raise jwt.InvalidTokenError("Invalid token header") from exc

    token_kid = header.get("kid")

    last_exc: Exception | None = None
    for key in keys:
        if token_kid and key.kid != token_kid:
            continue
        try:
            return jwt.decode(
                token,
                key.public_key,
                algorithms=["EdDSA"],
                options={"require": ["iss", "sub", "tnt", "sid", "iat", "exp"]},
                leeway=timedelta(seconds=30),
            )
        except jwt.InvalidTokenError as exc:
            last_exc = exc
            continue

    raise jwt.InvalidTokenError("Token verification failed") from last_exc


# --- Refresh tokens ---


def generate_refresh_token() -> tuple[str, str]:
    """Return (opaque_token, sha256_hash). Only the hash is stored."""
    random_part = secrets.token_urlsafe(32)
    opaque = f"rt_{uuid.uuid4().hex}_{random_part}"
    return opaque, hashlib.sha256(opaque.encode()).hexdigest()


def hash_refresh_token(opaque_token: str) -> str:
    return hashlib.sha256(opaque_token.encode()).hexdigest()

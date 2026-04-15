"""Auth Service — Build Order #4.

Depends on: Secrets & Config, Observability, DB Models.
"""

from .middleware import AuthContext, require_auth
from .routes import router as auth_router
from .service import (
    AuthError,
    InvalidCredentials,
    InvalidRefreshToken,
    SessionRevoked,
    TokenBundle,
    change_password,
    create_user,
    login,
    logout,
    refresh,
    revoke_user_sessions,
)
from .tokens import generate_ephemeral_keys, init_signing_keys, reset_keyring

__all__ = [
    "AuthContext",
    "auth_router",
    "require_auth",
    "AuthError",
    "InvalidCredentials",
    "InvalidRefreshToken",
    "SessionRevoked",
    "TokenBundle",
    "create_user",
    "login",
    "logout",
    "refresh",
    "change_password",
    "revoke_user_sessions",
    "init_signing_keys",
    "generate_ephemeral_keys",
    "reset_keyring",
]

import importlib
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth import service as auth_service
import app.secrets_config as secrets_config
from app.auth.middleware import AuthContext
from app.auth.service import InvalidRefreshToken
from app.auth.tokens import generate_ephemeral_keys, reset_keyring


class _ScalarResult:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = many or []

    def scalar_one_or_none(self):
        return self._one

    def scalar_one(self):
        return self._one

    def scalars(self):
        return SimpleNamespace(all=lambda: self._many)


class _AuthDB:
    def __init__(self):
        self.added = []
        self.flushed = 0
        self.next_execute_results = []

    async def execute(self, stmt):
        if self.next_execute_results:
            return self.next_execute_results.pop(0)
        return _ScalarResult()

    def add(self, obj):
        self.added.append(obj)
        cls_name = obj.__class__.__name__
        if cls_name == "AuthSession" and not getattr(obj, "session_id", None):
            obj.session_id = f"sess_{uuid.uuid4().hex}"
        if cls_name == "RefreshToken" and not getattr(obj, "token_id", None):
            obj.token_id = f"rt_{uuid.uuid4().hex}"
        if cls_name == "AuthAuditLog" and not getattr(obj, "audit_id", None):
            obj.audit_id = f"aud_{uuid.uuid4().hex}"

    async def flush(self):
        self.flushed += 1


def _auth_config():
    return SimpleNamespace(
        auth=SimpleNamespace(
            refresh_ttl_s=60 * 60 * 24 * 30,
            access_ttl_s=900,
            issuer="deal-screener",
            argon2=SimpleNamespace(memory_kib=65536, iterations=3, parallelism=1),
        )
    )


def _import_main():
    for name in [
        "app.main",
        "app.api.deals",
        "app.api.criteria",
        "app.api.deps",
        "app.database",
    ]:
        sys.modules.pop(name, None)

    with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=object()), patch(
        "sqlalchemy.ext.asyncio.async_sessionmaker", return_value=lambda: None
    ):
        return importlib.import_module("app.main"), importlib.import_module("app.database")


@pytest.fixture(autouse=True)
def _reset_keys():
    reset_keyring()
    yield
    reset_keyring()


def test_login_success_creates_session_refresh_token_and_audit(monkeypatch):
    db = _AuthDB()
    user = SimpleNamespace(
        user_id="usr_test",
        tenant_id="tnt_test",
        email="analyst@fund.com",
        is_active=True,
        password_hash="argon-hash",
    )
    db.next_execute_results = [_ScalarResult(one=user)]

    monkeypatch.setattr("app.secrets_config.get_config", lambda: _auth_config())
    monkeypatch.setattr(auth_service, "verify_password", lambda password, password_hash: True)
    generate_ephemeral_keys()

    bundle = asyncio.run(
        auth_service.login(
            db,
            email="analyst@fund.com",
            password="correct horse battery staple",
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    )

    assert bundle.user_id == user.user_id
    assert bundle.tenant_id == user.tenant_id
    assert bundle.expires_in == 900
    assert bundle.refresh_token.startswith("rt_")
    assert any(obj.__class__.__name__ == "AuthSession" for obj in db.added)
    assert any(obj.__class__.__name__ == "RefreshToken" for obj in db.added)
    assert [obj.event_type for obj in db.added if hasattr(obj, "event_type")] == ["LOGIN_SUCCESS"]


def test_refresh_reuse_detection_revokes_session_and_audits(monkeypatch):
    db = _AuthDB()
    rt = SimpleNamespace(
        token_id="rt_parent",
        session_id="sess_123",
        consumed_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    session = SimpleNamespace(
        session_id="sess_123",
        user_id="usr_test",
        tenant_id="tnt_test",
        revoked_at=None,
        revoked_reason=None,
    )
    db.next_execute_results = [_ScalarResult(one=rt), _ScalarResult(one=session)]
    monkeypatch.setattr(secrets_config, "get_config", lambda: _auth_config())

    with pytest.raises(InvalidRefreshToken):
        asyncio.run(auth_service.refresh(db, refresh_token="rt_reused", ip_address="127.0.0.1"))

    assert session.revoked_at is not None
    assert session.revoked_reason == "reuse"
    assert [obj.event_type for obj in db.added if hasattr(obj, "event_type")] == ["REUSE_DETECTED"]


def test_logout_route_requires_authorization_header_per_spec():
    main, database = _import_main()
    auth_routes = importlib.import_module("app.auth.routes")

    async def _override_db():
        yield _AuthDB()

    main.app.dependency_overrides[database.get_db] = _override_db
    called = {"count": 0}

    async def _fake_logout(db, refresh_token):
        called["count"] += 1

    with patch.object(auth_routes.service, "logout", _fake_logout):
        client = TestClient(main.app)
        response = client.post("/api/v1/auth/logout", json={"refresh_token": "rt_fake"})

    assert called["count"] == 0
    assert response.status_code == 401


def test_protected_deals_route_requires_auth_not_only_tenant_headers():
    main, database = _import_main()

    async def _override_db():
        yield _AuthDB()

    main.app.dependency_overrides[database.get_db] = _override_db
    client = TestClient(main.app)

    response = client.get(
        "/api/v1/deals/",
        headers={
            "X-Tenant-ID": str(uuid.uuid4()),
            "X-User-ID": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 401


def test_auth_context_includes_roles_per_spec():
    assert "roles" in AuthContext.__annotations__

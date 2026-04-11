import importlib
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient


def _import_api_modules():
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
        main = importlib.import_module("app.main")
        deals = importlib.import_module("app.api.deals")
        criteria = importlib.import_module("app.api.criteria")
        deps = importlib.import_module("app.api.deps")
        database = importlib.import_module("app.database")
    return main, deals, criteria, deps, database


class _EmptyResult:
    def scalars(self):
        return SimpleNamespace(all=lambda: [])

    def scalar_one_or_none(self):
        return None


class _EmptyDB:
    async def execute(self, stmt):
        return _EmptyResult()


def test_request_id_middleware_adds_header():
    main, deals, criteria, deps, database = _import_api_modules()
    main.app.dependency_overrides[database.get_db] = lambda: iter(())
    client = TestClient(main.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert response.json() == {"status": "ok"}


def test_global_exception_handler_returns_structured_error():
    main, deals, criteria, deps, database = _import_api_modules()

    @main.app.get("/boom")
    async def boom():
        raise RuntimeError("unexpected")

    client = TestClient(main.app, raise_server_exceptions=False)
    response = client.get("/boom")

    body = response.json()
    assert response.status_code == 500
    assert body["error"] == "internal_server_error"
    assert body["message"] == "An unexpected error occurred."
    assert "request_id" in body
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_header_deps_accept_valid_uuids_and_reject_invalid_or_missing():
    main, deals, criteria, deps, database = _import_api_modules()
    async def _override_db():
        yield _EmptyDB()

    main.app.dependency_overrides[database.get_db] = _override_db
    client = TestClient(main.app)

    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    ok = client.get(
        "/api/v1/deals/",
        headers={"X-Tenant-ID": tenant_id, "X-User-ID": user_id},
    )
    invalid = client.get(
        "/api/v1/deals/",
        headers={"X-Tenant-ID": "bad-uuid", "X-User-ID": user_id},
    )
    missing = client.get("/api/v1/deals/")

    assert ok.status_code == 200
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "Invalid X-Tenant-ID header. Must be a UUID."
    assert missing.status_code == 422

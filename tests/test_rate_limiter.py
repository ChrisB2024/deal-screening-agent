import importlib
import sys
import uuid
from ipaddress import ip_network
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.auth.middleware import AuthContext
from app.rate_limiter.bucket import BucketConfig, BucketState, check_and_consume, refill
from app.rate_limiter.config import EndpointGroup, LimitsTable, Scope
from app.rate_limiter.ip_parser import get_client_ip
from app.rate_limiter.middleware import RateLimitMiddleware, init_rate_limiter
from app.rate_limiter.store import InMemoryStore, RateLimitStore


def test_token_bucket_denies_after_burst_and_refills_over_time():
    config = BucketConfig.per_minute(5, 5)
    state = BucketState(tokens=5.0, last_refill_at=0.0)

    results = []
    for _ in range(5):
        result, state = check_and_consume(state, config, now=0.0)
        results.append(result.allowed)

    denied, state = check_and_consume(state, config, now=0.0)
    after_refill, state = check_and_consume(state, config, now=12.0)

    assert results == [True, True, True, True, True]
    assert denied.allowed is False
    assert denied.retry_after_seconds == 12
    assert after_refill.allowed is True


def test_client_ip_parser_ignores_spoofed_forwarded_for_from_untrusted_peer():
    app = FastAPI()

    @app.get("/")
    async def root():
        return {}

    client = TestClient(app)
    request = client.build_request(
        "GET",
        "/",
        headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.5"},
    )

    with client:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.9, 198.51.100.5")],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "query_string": b"",
            "server": ("testserver", 80),
            "root_path": "",
            "http_version": "1.1",
            "app": app,
        }
        from starlette.requests import Request

        req = Request(scope)
        assert get_client_ip(req, trusted_proxies=[]) == "127.0.0.1"


def test_client_ip_parser_uses_first_untrusted_hop_from_right():
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.2, 10.0.0.1")],
        "client": ("10.0.0.1", 12345),
        "scheme": "http",
        "query_string": b"",
        "server": ("testserver", 80),
        "root_path": "",
        "http_version": "1.1",
        "app": FastAPI(),
    }
    req = Request(scope)

    client_ip = get_client_ip(req, trusted_proxies=[ip_network("10.0.0.0/8")])

    assert client_ip == "203.0.113.9"


class _FailingStore(RateLimitStore):
    async def check(self, key: str, config: BucketConfig, cost: int = 1):
        raise RuntimeError("store down")

    async def reset(self, key: str) -> None:
        return None

    async def get_state(self, key: str):
        return None


def test_rate_limiter_fails_open_when_store_errors():
    app = FastAPI()
    init_rate_limiter(store=_FailingStore(), limits=LimitsTable.from_defaults(), trusted_proxies=[])
    app.add_middleware(RateLimitMiddleware)

    @app.get("/limited")
    async def limited():
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/limited")

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Status"] == "degraded"


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


class _RouteDB:
    async def execute(self, stmt):
        return SimpleNamespace(
            scalar_one_or_none=lambda: None,
            scalar_one=lambda: None,
            scalars=lambda: SimpleNamespace(all=lambda: []),
        )

    def add(self, obj):
        return None

    async def flush(self):
        return None


def test_upload_route_is_rate_limited_by_user_scope(monkeypatch):
    main, database = _import_main()
    from app.auth.middleware import require_auth
    from app.auth.tokens import generate_ephemeral_keys, create_access_token
    from app.rate_limiter.middleware import init_rate_limiter

    generate_ephemeral_keys()

    user_id = f"usr_{uuid.uuid4().hex}"
    tenant_id = str(uuid.uuid4())
    session_id = f"sess_{uuid.uuid4().hex}"
    token = create_access_token(
        user_id=user_id, tenant_id=tenant_id, session_id=session_id, roles=[]
    )

    async def _override_db():
        yield _RouteDB()

    async def _override_auth():
        return AuthContext(
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            roles=[],
        )

    async def _ok_ingest(**kwargs):
        from app.models.enums import DealStatus
        from app.services.ingestion_service import IngestionResult

        return IngestionResult(
            deal_id=uuid.uuid4(),
            status=DealStatus.SCORED,
            message="ok",
            is_duplicate=False,
        )

    main.app.dependency_overrides[database.get_db] = _override_db
    main.app.dependency_overrides[require_auth] = _override_auth
    import app.api.deals as deals_module

    monkeypatch.setattr(deals_module, "validate_pdf", lambda f: f)
    monkeypatch.setattr(deals_module, "ingest_deal", _ok_ingest)

    limits = LimitsTable()
    limits.set(Scope.USER, EndpointGroup.UPLOAD, BucketConfig.per_minute(1, 1))
    limits.set(Scope.TENANT, EndpointGroup.UPLOAD, BucketConfig.per_minute(1, 1))
    init_rate_limiter(store=InMemoryStore(), limits=limits, trusted_proxies=[])

    client = TestClient(main.app)
    first = client.post(
        "/api/v1/deals/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("deal.pdf", b"%PDF-1.4", "application/pdf")},
    )
    second = client.post(
        "/api/v1/deals/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("deal.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert first.status_code == 202
    assert second.status_code == 429


def test_health_endpoints_are_not_rate_limited():
    app = FastAPI()
    limits = LimitsTable()
    limits.set(Scope.IP, EndpointGroup.DEFAULT, BucketConfig.per_minute(1, 1))
    init_rate_limiter(store=InMemoryStore(), limits=limits, trusted_proxies=[])
    app.add_middleware(RateLimitMiddleware)

    @app.get("/health/liveness")
    async def liveness():
        return {"status": "alive"}

    client = TestClient(app)
    first = client.get("/health/liveness")
    second = client.get("/health/liveness")

    assert first.status_code == 200
    assert second.status_code == 200

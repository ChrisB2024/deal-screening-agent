import importlib
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.models.enums import ConfidenceLevel, DealStatus, DecisionType, FieldExtractionStatus
from app.services.ingestion_service import IngestionError, IngestionResult
from app.services.scoring_service import ScoringResult


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
        database = importlib.import_module("app.database")
    return main, deals, criteria, database


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


class _RouteDB:
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
        if obj.__class__.__name__ == "CriteriaConfig" and getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
            obj.criteria = []
        if obj.__class__.__name__ == "Criterion" and getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        self.flushed += 1


def _client_and_modules():
    main, deals, criteria, database = _import_api_modules()
    from app.auth.middleware import AuthContext, require_auth
    db = _RouteDB()
    auth_ctx = AuthContext(
        user_id=f"usr_{uuid.uuid4().hex}",
        tenant_id=str(uuid.uuid4()),
        session_id=f"sess_{uuid.uuid4().hex}",
        roles=[],
    )

    async def _override_db():
        yield db

    async def _override_auth():
        return auth_ctx

    main.app.dependency_overrides[database.get_db] = _override_db
    main.app.dependency_overrides[require_auth] = _override_auth
    client = TestClient(main.app)
    return client, db, deals, criteria, auth_ctx


def _headers():
    return {"Authorization": "Bearer test-token"}


def test_upload_route_returns_upload_response_and_maps_ingestion_error(monkeypatch):
    client, db, deals, criteria, auth = _client_and_modules()
    headers = _headers()

    async def _ok_ingest(**kwargs):
        return IngestionResult(
            deal_id=uuid.uuid4(),
            status=DealStatus.UPLOADED,
            message="Deal uploaded. Extraction and scoring queued for processing.",
            is_duplicate=False,
        )

    async def _bad_ingest(**kwargs):
        raise IngestionError("bad file")

    monkeypatch.setattr(deals, "validate_pdf", lambda f: f)
    monkeypatch.setattr(deals, "ingest_deal", _ok_ingest)
    ok = client.post(
        "/api/v1/deals/upload",
        headers=headers,
        files={"file": ("deal.pdf", b"%PDF-1.4", "application/pdf")},
    )

    monkeypatch.setattr(deals, "ingest_deal", _bad_ingest)
    bad = client.post(
        "/api/v1/deals/upload",
        headers=headers,
        files={"file": ("deal.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert ok.status_code == 202
    assert ok.json()["status"] == "UPLOADED"
    assert "deal_id" in ok.json()
    assert bad.status_code == 400
    assert bad.json()["detail"] == "bad file"


def test_list_and_get_deal_routes_return_cards_and_404_for_wrong_tenant(monkeypatch):
    client, db, deals, criteria, auth = _client_and_modules()
    headers = _headers()
    deal = SimpleNamespace(
        id=uuid.uuid4(),
        filename="deal.pdf",
        status=DealStatus.SCORED,
        source_channel="upload",
        created_at=datetime.now(timezone.utc),
    )

    db.next_execute_results = [_ScalarResult(many=[deal])]
    missing_id = uuid.UUID("00000000-0000-0000-0000-000000000000")

    async def _build_card(db_arg, deal_arg):
        return {
            "id": str(deal_arg.id),
            "filename": deal_arg.filename,
            "status": deal_arg.status.value,
            "source_channel": deal_arg.source_channel,
            "created_at": deal_arg.created_at.isoformat(),
            "extracted_fields": None,
            "extraction_confidence": None,
            "score": 88,
            "score_confidence": "HIGH",
            "rationale": "Good fit",
            "decision": None,
            "decision_notes": None,
            "decided_at": None,
        }

    async def _get_or_404(db_arg, deal_id, tenant_id):
        if deal_id == missing_id:
            raise deals.HTTPException(status_code=404, detail="Deal not found.")
        return deal

    monkeypatch.setattr(deals, "_build_deal_card", _build_card)
    monkeypatch.setattr(deals, "_get_deal_or_404", _get_or_404)

    listed = client.get("/api/v1/deals/?sort_by=score&limit=10&offset=0", headers=headers)
    found = client.get(f"/api/v1/deals/{deal.id}", headers=headers)
    missing = client.get(f"/api/v1/deals/{missing_id}", headers=headers)

    assert listed.status_code == 200
    assert listed.json()[0]["score"] == 88
    assert found.status_code == 200
    assert found.json()["id"] == str(deal.id)
    assert missing.status_code == 404


def test_decide_route_requires_scored_status_and_creates_decision_and_audit(monkeypatch):
    client, db, deals, criteria, auth = _client_and_modules()
    headers = _headers()
    scored_deal = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), status=DealStatus.SCORED)
    pending_deal = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), status=DealStatus.EXTRACTED)
    latest_score = SimpleNamespace(id=uuid.uuid4())

    async def _get_or_404(db_arg, deal_id, tenant_id):
        if deal_id == pending_deal.id:
            return pending_deal
        return scored_deal

    async def _latest_score(db_arg, deal_id):
        return latest_score

    monkeypatch.setattr(deals, "_get_deal_or_404", _get_or_404)
    monkeypatch.setattr(deals, "_get_latest_score", _latest_score)

    ok = client.post(
        f"/api/v1/deals/{scored_deal.id}/decide",
        headers=headers,
        json={"decision": "PASSED", "notes": "Outside thesis"},
    )
    bad = client.post(
        f"/api/v1/deals/{pending_deal.id}/decide",
        headers=headers,
        json={"decision": "PASSED", "notes": "Outside thesis"},
    )

    assert ok.status_code == 200
    assert ok.json()["status"] == "DECIDED"
    assert scored_deal.status is DealStatus.DECIDED
    assert [obj.action for obj in db.added if hasattr(obj, "action")] == ["DECISION_MADE"]
    assert bad.status_code == 400


def test_criteria_routes_create_get_and_list_history(monkeypatch):
    client, db, deals, criteria, auth = _client_and_modules()
    headers = _headers()
    tenant_id = auth.tenant_id
    created_config = None

    async def _latest_version(db_arg, tenant_arg):
        return 2

    async def _rescore(db_arg, tenant_arg):
        return None

    def _config_response(config):
        return {
            "id": str(config.id),
            "tenant_id": str(config.tenant_id),
            "version": config.version,
            "is_active": config.is_active,
            "name": config.name,
            "criteria": [
                {
                    "id": str(c.id),
                    "field_name": c.field_name,
                    "criterion_type": c.criterion_type.value,
                    "operator": c.operator,
                    "target_value": c.target_value,
                    "weight": c.weight,
                    "label": c.label,
                }
                for c in getattr(config, "criteria", [])
            ],
        }

    monkeypatch.setattr(criteria, "_get_latest_version", _latest_version)
    monkeypatch.setattr(criteria, "_rescore_extracted_deals", _rescore)
    monkeypatch.setattr(criteria, "_config_to_response", _config_response)

    async def _execute(stmt):
        nonlocal created_config
        if created_config is None:
            return _ScalarResult()
        return _ScalarResult(one=created_config, many=[created_config])

    db.execute = _execute

    original_add = db.add

    def _add(obj):
        nonlocal created_config
        original_add(obj)
        if obj.__class__.__name__ == "CriteriaConfig":
            obj.id = uuid.uuid4()
            obj.tenant_id = uuid.UUID(tenant_id)
            obj.criteria = []
            created_config = obj
        if obj.__class__.__name__ == "Criterion":
            obj.id = uuid.uuid4()
            created_config.criteria.append(obj)

    db.add = _add

    body = {
        "name": "v3",
        "criteria": [
            {
                "field_name": "sector",
                "criterion_type": "MUST_HAVE",
                "operator": "eq",
                "target_value": "\"healthcare\"",
                "weight": 1.0,
                "label": "Sector fit",
            }
        ],
    }

    created = client.post("/api/v1/criteria/config", headers=headers, json=body)
    active = client.get("/api/v1/criteria/config", headers=headers)
    history = client.get("/api/v1/criteria/config/history", headers=headers)

    assert created.status_code == 201
    assert created.json()["version"] == 3
    assert created.json()["is_active"] is True
    assert created.json()["criteria"][0]["field_name"] == "sector"
    assert active.status_code == 200
    assert active.json()["version"] == 3
    assert history.status_code == 200
    assert history.json()[0]["version"] == 3


def test_active_config_returns_404_when_none_and_tenant_isolation_holds(monkeypatch):
    client, db, deals, criteria, auth = _client_and_modules()
    headers = _headers()

    async def _none_execute(stmt):
        return _ScalarResult(one=None, many=[])

    db.execute = _none_execute

    response = client.get("/api/v1/criteria/config", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "No active criteria config found."

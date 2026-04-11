import importlib
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def _import_main():
    for name in ["app.main", "app.api.deals", "app.api.criteria", "app.api.deps", "app.database"]:
        sys.modules.pop(name, None)

    with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=object()), patch(
        "sqlalchemy.ext.asyncio.async_sessionmaker", return_value=lambda: None
    ):
        return importlib.import_module("app.main")


def test_cors_allows_localhost_frontend_origin():
    main = _import_main()
    client = TestClient(main.app)

    response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_vite_config_contains_api_proxy_and_src_alias():
    vite_config = (ROOT / "frontend" / "vite.config.ts").read_text()

    assert "'@': path.resolve(__dirname, './src')" in vite_config
    assert "'/api': 'http://localhost:8000'" in vite_config


def test_initial_migration_creates_expected_core_tables_and_constraints():
    migration = (ROOT / "alembic" / "versions" / "1ed7e8f2e432_initial_schema.py").read_text()

    for table_name in [
        "criteria_configs",
        "deals",
        "audit_logs",
        "criteria",
        "deal_scores",
        "extracted_fields",
        "deal_decisions",
    ]:
        assert f"op.create_table('{table_name}'" in migration

    assert "uq_criteria_configs_tenant_version" in migration
    assert "uq_deals_tenant_content_hash" in migration

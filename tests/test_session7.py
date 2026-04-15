import importlib
import json
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_secrets_config_imports_and_supports_memory_bootstrap():
    for name in list(sys.modules):
        if name == "app.secrets_config" or name.startswith("app.secrets_config."):
            sys.modules.pop(name, None)

    module = importlib.import_module("app.secrets_config")

    assert hasattr(module, "bootstrap")
    assert hasattr(module, "get_config")
    assert hasattr(module, "get_secrets")


def test_main_exposes_metrics_endpoint_per_observability_spec():
    for name in ["app.main", "app.api.deals", "app.api.criteria", "app.api.deps", "app.database"]:
        sys.modules.pop(name, None)

    with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=object()), patch(
        "sqlalchemy.ext.asyncio.async_sessionmaker", return_value=lambda: None
    ):
        main = importlib.import_module("app.main")

    client = TestClient(main.app)
    response = client.get("/metrics")

    assert response.status_code == 200


def test_structured_logger_promotes_duration_ms_to_top_level(capsys):
    from app.observability.logger import get_logger

    logger = get_logger("session7_test")
    logger.info("timed.event", duration_ms=123, note="ok")

    captured = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(captured)

    assert payload["duration_ms"] == 123
    assert payload["fields"]["note"] == "ok"
    assert "duration_ms" not in payload.get("fields", {})


def test_legacy_audit_action_enum_removed_after_string_action_migration():
    from app.models import enums

    assert not hasattr(enums, "AuditAction")

import importlib
import io
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.input_validation import (
    InputValidationMiddleware,
    ReasonCode,
    ValidationFailure,
    validate_file,
    validate_pdf,
)
from app.input_validation.file_validator import MAX_FILE_SIZE_BYTES


def test_reason_code_enum_matches_closed_contract():
    expected = {
        "BODY_TOO_LARGE",
        "HEADER_TOO_LARGE",
        "TOO_MANY_PARTS",
        "UNSUPPORTED_CONTENT_TYPE",
        "MIME_MAGIC_MISMATCH",
        "MALFORMED_PDF",
        "PDF_CONTAINS_JAVASCRIPT",
        "PDF_ENCRYPTED",
        "PDF_TOO_MANY_PAGES",
        "DECOMPRESSION_BOMB",
        "COMPRESSION_RATIO_EXCEEDED",
        "SCHEMA_VIOLATION",
        "WEBHOOK_MALFORMED",
    }

    assert {reason.value for reason in ReasonCode} == expected


def test_validate_file_accepts_exact_size_limit_and_rejects_one_byte_over():
    exact = validate_file(b"%PDF-" + b"a" * (MAX_FILE_SIZE_BYTES - 5), "application/pdf")
    over = validate_file(b"%PDF-" + b"a" * MAX_FILE_SIZE_BYTES, "application/pdf")

    assert not isinstance(exact, ValidationFailure)
    assert isinstance(over, ValidationFailure)
    assert over.reason is ReasonCode.BODY_TOO_LARGE


def test_validate_file_rejects_unsupported_content_type():
    result = validate_file(b"%PDF-1.4", "image/png")

    assert isinstance(result, ValidationFailure)
    assert result.reason is ReasonCode.UNSUPPORTED_CONTENT_TYPE
    assert result.http_status == 415


def test_validate_file_rejects_magic_mismatch():
    result = validate_file(b"PK\x03\x04not-a-pdf", "application/pdf")

    assert isinstance(result, ValidationFailure)
    assert result.reason is ReasonCode.MIME_MAGIC_MISMATCH
    assert result.http_status == 415


def _make_pdf(*, encrypt: bool = False, pages: int = 1, add_js: bool = False) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if add_js:
        writer.add_js("app.alert('x')")
    if encrypt:
        writer.encrypt("secret")

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_validate_pdf_rejects_encrypted_pdf():
    file_result = validate_file(_make_pdf(encrypt=True), "application/pdf")
    assert not isinstance(file_result, ValidationFailure)

    result = validate_pdf(file_result)

    assert isinstance(result, ValidationFailure)
    assert result.reason is ReasonCode.PDF_ENCRYPTED
    assert result.http_status == 422


def test_validate_pdf_rejects_javascript_pdf():
    file_result = validate_file(_make_pdf(add_js=True), "application/pdf")
    assert not isinstance(file_result, ValidationFailure)

    result = validate_pdf(file_result)

    assert isinstance(result, ValidationFailure)
    assert result.reason is ReasonCode.PDF_CONTAINS_JAVASCRIPT


def test_validate_pdf_rejects_page_count_over_limit(monkeypatch):
    import app.input_validation.pdf_validator as pdf_validator

    monkeypatch.setattr(pdf_validator, "MAX_PDF_PAGES", 1)
    file_result = validate_file(_make_pdf(pages=2), "application/pdf")
    assert not isinstance(file_result, ValidationFailure)

    result = validate_pdf(file_result)

    assert isinstance(result, ValidationFailure)
    assert result.reason is ReasonCode.PDF_TOO_MANY_PAGES


def test_validate_pdf_rejects_malformed_pdf():
    file_result = validate_file(b"%PDF-not-actually-a-pdf", "application/pdf")
    assert not isinstance(file_result, ValidationFailure)

    result = validate_pdf(file_result)

    assert isinstance(result, ValidationFailure)
    assert result.reason is ReasonCode.MALFORMED_PDF
    assert result.http_status == 422


def test_input_validation_middleware_rejects_large_declared_upload_body():
    app = FastAPI()
    app.add_middleware(InputValidationMiddleware)

    @app.post("/api/v1/deals/upload")
    async def upload():
        return {"ok": True}

    client = TestClient(app)
    response = client.post(
        "/api/v1/deals/upload",
        headers={"Content-Length": str(52 * 1024 * 1024)},
        content=b"",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == ReasonCode.BODY_TOO_LARGE.value
    assert "X-Request-ID" in response.headers


def test_input_validation_middleware_uses_smaller_limit_for_non_upload_paths():
    app = FastAPI()
    app.add_middleware(InputValidationMiddleware)

    @app.post("/api/v1/auth/login")
    async def login():
        return {"ok": True}

    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        headers={"Content-Length": str(2 * 1024 * 1024)},
        content=b"",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == ReasonCode.BODY_TOO_LARGE.value


def test_input_validation_middleware_exempts_health_routes():
    app = FastAPI()
    app.add_middleware(InputValidationMiddleware)

    @app.get("/health/liveness")
    async def liveness():
        return {"status": "alive"}

    client = TestClient(app)
    response = client.get(
        "/health/liveness",
        headers={"Content-Length": str(999999999)},
    )

    assert response.status_code == 200


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


def test_upload_route_rejects_invalid_pdf_before_ingestion(monkeypatch):
    main, database = _import_main()
    from app.auth.middleware import require_auth, AuthContext

    async def _override_db():
        yield _RouteDB()

    async def _override_auth():
        return AuthContext(
            user_id=f"usr_{uuid.uuid4().hex}",
            tenant_id=str(uuid.uuid4()),
            session_id=f"sess_{uuid.uuid4().hex}",
            roles=[],
        )

    async def _ingest_should_not_run(**kwargs):
        raise AssertionError("ingest_deal should not run for invalid uploads")

    main.app.dependency_overrides[database.get_db] = _override_db
    main.app.dependency_overrides[require_auth] = _override_auth
    import app.api.deals as deals_module

    monkeypatch.setattr(deals_module, "ingest_deal", _ingest_should_not_run)

    client = TestClient(main.app)
    response = client.post(
        "/api/v1/deals/upload",
        headers={"Authorization": "Bearer irrelevant"},
        files={"file": ("deal.pdf", b"PK\x03\x04", "application/pdf")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "This doesn't appear to be a valid PDF."


def test_protected_upload_auth_runs_before_input_validation_per_spec():
    main, _database = _import_main()
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/deals/upload",
        headers={"Content-Length": str(52 * 1024 * 1024)},
        content=b"",
    )

    assert response.status_code == 401

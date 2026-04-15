import asyncio
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.enums import DealStatus
from app.services import ingestion_service
from app.services.ingestion_service import IngestionError, ingest_deal


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, duplicate=None):
        self.duplicate = duplicate
        self.added = []
        self.flushed = 0
        self.refreshed = []

    async def execute(self, stmt):
        return _FakeResult(self.duplicate)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)


def test_validate_file_type_rejects_bad_extension():
    with pytest.raises(IngestionError, match="Only PDF files are accepted"):
        ingestion_service._validate_file_type("deal.txt", "application/pdf")


def test_validate_file_type_rejects_bad_content_type():
    with pytest.raises(IngestionError, match="Expected application/pdf"):
        ingestion_service._validate_file_type("deal.pdf", "text/plain")


def test_store_file_sanitizes_path_traversal_filename(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(ingestion_service.settings, "upload_dir", str(tmp_path))

    deal_id = uuid.uuid4()
    stored = asyncio.run(
        ingestion_service._store_file(deal_id, "../../secret/deal.pdf", b"pdf-bytes")
    )

    assert stored.name == "deal.pdf"
    assert ".." not in str(stored)
    assert stored.parent == tmp_path / str(deal_id)
    assert stored.read_bytes() == b"pdf-bytes"


def test_ingest_deal_returns_existing_duplicate_for_same_tenant():
    tenant_id = uuid.uuid4()
    duplicate = SimpleNamespace(id=uuid.uuid4(), status=DealStatus.SCORED)
    db = _FakeDB(duplicate=duplicate)

    result = asyncio.run(
        ingest_deal(
            db=db,
            tenant_id=tenant_id,
            filename="deal.pdf",
            file_content=b"%PDF same",
            content_type="application/pdf",
        )
    )

    assert result.is_duplicate is True
    assert result.deal_id == duplicate.id
    assert result.status is DealStatus.SCORED
    assert db.added == []


def test_check_duplicate_query_is_tenant_scoped():
    tenant_id = uuid.uuid4()
    content_hash = hashlib.sha256(b"doc").hexdigest()
    db = _FakeDB()

    result = asyncio.run(ingestion_service._check_duplicate(db, tenant_id, content_hash))

    assert result is None


def test_ingest_deal_rejects_large_and_empty_files():
    db = _FakeDB()
    tenant_id = uuid.uuid4()

    with pytest.raises(IngestionError, match="Maximum size is"):
        asyncio.run(
            ingest_deal(
                db=db,
                tenant_id=tenant_id,
                filename="deal.pdf",
                file_content=b"x" * (ingestion_service.settings.max_file_size_bytes + 1),
                content_type="application/pdf",
            )
        )

    with pytest.raises(IngestionError, match="File is empty"):
        asyncio.run(
            ingest_deal(
                db=db,
                tenant_id=tenant_id,
                filename="deal.pdf",
                file_content=b"",
                content_type="application/pdf",
            )
        )


def test_ingest_deal_enqueues_extraction_job_and_returns_uploaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(ingestion_service.settings, "upload_dir", str(tmp_path))
    tenant_id = uuid.uuid4()
    db = _FakeDB()
    enqueued_calls = []

    async def _fake_enqueue(db_arg, *, job_type, payload, **kwargs):
        enqueued_calls.append({"job_type": job_type, "payload": payload, **kwargs})
        return "fake-job-id"

    import app.background_jobs as bg_jobs
    monkeypatch.setattr(bg_jobs, "enqueue", _fake_enqueue)

    result = asyncio.run(
        ingest_deal(
            db=db,
            tenant_id=tenant_id,
            filename="../broker/deal.pdf",
            file_content=b"%PDF-1.4 deal content",
            content_type="application/pdf",
            source_channel="upload",
        )
    )

    created_deal = next(obj for obj in db.added if obj.__class__.__name__ == "Deal")
    audit = next(obj for obj in db.added if getattr(obj, "action", None) == "DEAL_UPLOADED")

    assert result.is_duplicate is False
    assert result.status is DealStatus.UPLOADED
    assert "queued" in result.message.lower()
    assert created_deal.filename == "../broker/deal.pdf"
    assert Path(created_deal.file_path).name == "deal.pdf"
    assert audit.after_state == DealStatus.UPLOADED.value
    assert audit.actor_type == "system"
    assert db.flushed == 1

    assert len(enqueued_calls) == 1
    assert enqueued_calls[0]["job_type"] == "extraction"
    assert enqueued_calls[0]["payload"]["deal_id"] == str(created_deal.id)
    assert enqueued_calls[0]["payload"]["tenant_id"] == str(tenant_id)

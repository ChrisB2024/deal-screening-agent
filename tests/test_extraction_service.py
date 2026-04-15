import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import ConfidenceLevel, DealStatus
from app.services import extraction_service
from app.services.extraction_service import ExtractionResult, extract_deal
from app.services.llm_client import LLMExtractionError
from app.services.pdf_parser import PDFParseError


def _llm_fields(found_count: int, confidence: str = "HIGH") -> dict:
    names = ["sector", "revenue", "ebitda", "geography", "ask_price", "deal_type"]
    fields = []
    for index, name in enumerate(names):
        if index < found_count:
            fields.append(
                {
                    "field_name": name,
                    "field_value": f"value-{index}",
                    "field_status": "FOUND",
                    "confidence": confidence,
                }
            )
        else:
            fields.append(
                {
                    "field_name": name,
                    "field_value": None,
                    "field_status": "MISSING",
                    "confidence": "LOW",
                }
            )
    return {"fields": fields}


class _FakeResult:
    def __init__(self, deal):
        self._deal = deal

    def scalar_one_or_none(self):
        return self._deal


class _FakeDB:
    def __init__(self, deal):
        self.deal = deal
        self.added = []
        self.flushed = 0
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(self.deal)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


def test_compute_overall_confidence_matches_builder_rules():
    assert (
        extraction_service._compute_overall_confidence(_llm_fields(6, "HIGH")["fields"], 6)
        is ConfidenceLevel.HIGH
    )
    assert (
        extraction_service._compute_overall_confidence(_llm_fields(4, "MEDIUM")["fields"], 4)
        is ConfidenceLevel.MEDIUM
    )
    assert (
        extraction_service._compute_overall_confidence(_llm_fields(3, "LOW")["fields"], 3)
        is ConfidenceLevel.LOW
    )
    assert (
        extraction_service._compute_overall_confidence(_llm_fields(2, "HIGH")["fields"], 2)
        is ConfidenceLevel.NONE
    )


def test_extract_deal_marks_deal_extracted_and_logs_audit(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid.uuid4()
    deal = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        file_path="/tmp/deal.pdf",
        status=DealStatus.UPLOADED,
        retry_count=0,
    )
    db = _FakeDB(deal)

    monkeypatch.setattr(extraction_service, "extract_text_from_pdf", lambda _: "raw text")
    monkeypatch.setattr(extraction_service, "scrub_pii", lambda text: f"scrubbed::{text}")

    async def _fake_extract(scrubbed_text):
        assert scrubbed_text == "scrubbed::raw text"
        return _llm_fields(3, "LOW")

    monkeypatch.setattr(extraction_service, "extract_fields_via_llm", _fake_extract)

    result = asyncio.run(extract_deal(db, deal.id, tenant_id))

    assert isinstance(result, ExtractionResult)
    assert result.success is True
    assert result.fields_found == 3
    assert result.overall_confidence is ConfidenceLevel.LOW
    assert deal.status is DealStatus.EXTRACTED
    assert db.flushed == 1
    assert len([entry for entry in db.added if entry.__class__.__name__ == "ExtractedField"]) == 6
    assert [entry.action for entry in db.added if hasattr(entry, "action")] == [
        "EXTRACTION_STARTED",
        "EXTRACTION_COMPLETED",
    ]


def test_extract_deal_marks_failed_when_threshold_not_met(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid.uuid4()
    deal = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        file_path="/tmp/deal.pdf",
        status=DealStatus.UPLOADED,
        retry_count=0,
    )
    db = _FakeDB(deal)

    monkeypatch.setattr(extraction_service, "extract_text_from_pdf", lambda _: "raw text")
    monkeypatch.setattr(extraction_service, "scrub_pii", lambda text: text)
    monkeypatch.setattr(
        extraction_service,
        "extract_fields_via_llm",
        lambda _: asyncio.sleep(0, result=_llm_fields(2, "HIGH")),
    )

    result = asyncio.run(extract_deal(db, deal.id, tenant_id))

    assert result.success is False
    assert "Only 2/6 fields extracted" in result.error
    assert deal.status is DealStatus.FAILED
    assert deal.retry_count == 1
    assert [entry.action for entry in db.added if hasattr(entry, "action")] == [
        "EXTRACTION_STARTED",
        "EXTRACTION_FAILED",
    ]


def test_extract_deal_rejects_wrong_tenant():
    deal = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        file_path="/tmp/deal.pdf",
        status=DealStatus.UPLOADED,
        retry_count=0,
    )
    db = _FakeDB(None)

    with pytest.raises(ValueError, match="not found for tenant"):
        asyncio.run(extract_deal(db, deal.id, deal.tenant_id))


def test_extract_deal_marks_failed_on_pdf_or_llm_errors(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid.uuid4()
    deal = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        file_path="/tmp/deal.pdf",
        status=DealStatus.UPLOADED,
        retry_count=0,
    )
    db = _FakeDB(deal)

    monkeypatch.setattr(
        extraction_service,
        "extract_text_from_pdf",
        lambda _: (_ for _ in ()).throw(PDFParseError("bad pdf")),
    )

    result = asyncio.run(extract_deal(db, deal.id, tenant_id))
    assert result.success is False
    assert result.error == "bad pdf"
    assert deal.status is DealStatus.FAILED

    deal.status = DealStatus.UPLOADED
    deal.retry_count = 0
    db = _FakeDB(deal)

    monkeypatch.setattr(extraction_service, "extract_text_from_pdf", lambda _: "raw text")
    monkeypatch.setattr(extraction_service, "scrub_pii", lambda text: text)

    async def _raise_llm(_):
        raise LLMExtractionError("llm failed")

    monkeypatch.setattr(extraction_service, "extract_fields_via_llm", _raise_llm)

    result = asyncio.run(extract_deal(db, deal.id, tenant_id))
    assert result.success is False
    assert result.error == "llm failed"
    assert deal.status is DealStatus.FAILED

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from app.services import llm_client
from app.services.llm_client import LLMExtractionError, extract_fields_via_llm


def _valid_payload() -> dict:
    return {
        "fields": [
            {"field_name": "sector", "field_value": "healthcare", "field_status": "FOUND", "confidence": "HIGH"},
            {"field_name": "revenue", "field_value": "5000000", "field_status": "FOUND", "confidence": "HIGH"},
            {"field_name": "ebitda", "field_value": "1200000", "field_status": "FOUND", "confidence": "HIGH"},
            {"field_name": "geography", "field_value": "US Southeast", "field_status": "FOUND", "confidence": "HIGH"},
            {"field_name": "ask_price", "field_value": "15000000", "field_status": "FOUND", "confidence": "HIGH"},
            {"field_name": "deal_type", "field_value": "acquisition", "field_status": "FOUND", "confidence": "HIGH"},
        ],
        "document_summary": "Healthcare services deal.",
        "extraction_notes": "Clean teaser.",
    }


def test_validate_extraction_response_accepts_valid_payload():
    llm_client._validate_extraction_response(_valid_payload())


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda p: p.pop("fields"), "missing 'fields'"),
        (lambda p: p.update(fields="bad"), "'fields' must be a list"),
        (lambda p: p["fields"].pop(), "missing fields"),
        (lambda p: p["fields"].append(dict(p["fields"][0])), "Duplicate field_name"),
        (lambda p: p["fields"][0].update(field_status="BAD"), "Invalid field_status"),
        (lambda p: p["fields"][0].update(confidence="NONE"), "Invalid confidence"),
        (lambda p: p["fields"][0].update(field_status="MISSING", field_value="x"), "MISSING but has a non-null value"),
        (lambda p: p["fields"][0].update(field_status="FOUND", field_value=None), "FOUND but has no value"),
    ],
)
def test_validate_extraction_response_rejects_invalid_payload(mutator, message: str):
    payload = _valid_payload()
    mutator(payload)

    with pytest.raises(LLMExtractionError, match=message):
        llm_client._validate_extraction_response(payload)


def test_extract_fields_via_llm_retries_timeouts_and_uses_json_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    calls: list[dict] = []
    sleeps: list[float] = []

    class _FakeCompletions:
        def __init__(self):
            self.attempt = 0

        async def create(self, **kwargs):
            calls.append(kwargs)
            self.attempt += 1
            if self.attempt < 3:
                raise APITimeoutError(request=request)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(_valid_payload()))
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))

    monkeypatch.setattr(llm_client, "_get_client", lambda: fake_client)

    real_sleep = asyncio.sleep

    async def _fake_sleep(seconds):
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    result = asyncio.run(extract_fields_via_llm("scrubbed doc"))

    assert result["fields"][0]["field_name"] == "sector"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]
    assert calls[0]["temperature"] == 0
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_extract_fields_via_llm_raises_on_invalid_json(monkeypatch: pytest.MonkeyPatch):
    class _FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{not-json"))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake_client)

    with pytest.raises(LLMExtractionError, match="invalid JSON"):
        asyncio.run(extract_fields_via_llm("scrubbed doc"))

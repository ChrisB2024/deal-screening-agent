"""OpenAI LLM client for deal field extraction.

Handles the API call with retry logic, timeout, and response validation.
Spec decisions: GPT-4o, temperature=0, structured output only.
Trust boundary: API → OpenAI (semi-trusted) — input is PII-scrubbed, output is validated.
"""

import json
import logging
from typing import Any

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from app.config import settings
from app.services.extraction_prompt import build_extraction_messages

logger = logging.getLogger(__name__)

# Retry config per spec: "Retry 2x with exponential backoff"
MAX_RETRIES = 2
INITIAL_BACKOFF_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30  # Spec: "30s timeout"


class LLMExtractionError(Exception):
    """Raised when the LLM fails to produce a valid extraction."""

    pass


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


async def extract_fields_via_llm(document_text: str) -> dict[str, Any]:
    """Call OpenAI to extract structured deal fields from document text.

    Purpose: Send PII-scrubbed text to GPT-4o and get structured field extraction.
    Inputs: Sanitized document text (already PII-scrubbed by caller).
    Outputs: Parsed JSON dict with 'fields', 'document_summary', 'extraction_notes'.
    Invariants: Temperature=0 for determinism. Always returns valid JSON or raises.
    Security: Input must be PII-scrubbed before calling. Response is validated as JSON.

    Raises:
        LLMExtractionError: After all retries exhausted, or if response is unparseable.
    """
    client = _get_client()
    messages = build_extraction_messages(document_text)

    last_error: Exception | None = None
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
            )

            raw_content = response.choices[0].message.content
            if not raw_content:
                raise LLMExtractionError("LLM returned empty response")

            parsed = json.loads(raw_content)
            _validate_extraction_response(parsed)
            return parsed

        except (APITimeoutError, RateLimitError, APIError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                logger.warning(
                    f"LLM call failed (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}. "
                    f"Retrying in {backoff}s..."
                )
                import asyncio
                await asyncio.sleep(backoff)
                backoff *= 2
            continue

        except json.JSONDecodeError as e:
            raise LLMExtractionError(f"LLM returned invalid JSON: {e}") from e

    raise LLMExtractionError(
        f"LLM extraction failed after {MAX_RETRIES + 1} attempts. Last error: {last_error}"
    )


def _validate_extraction_response(parsed: dict[str, Any]) -> None:
    """Validate the structure of the LLM extraction response.

    Purpose: Ensure the LLM output conforms to our expected schema.
    Invariants: Raises on any structural violation — never lets bad data through.
    """
    if "fields" not in parsed:
        raise LLMExtractionError("LLM response missing 'fields' key")

    fields = parsed["fields"]
    if not isinstance(fields, list):
        raise LLMExtractionError("'fields' must be a list")

    valid_field_names = {"sector", "revenue", "ebitda", "geography", "ask_price", "deal_type"}
    valid_statuses = {"FOUND", "INFERRED", "MISSING"}
    valid_confidences = {"HIGH", "MEDIUM", "LOW"}

    seen_fields = set()
    for field in fields:
        name = field.get("field_name")
        if name not in valid_field_names:
            raise LLMExtractionError(f"Unknown field_name: {name}")

        if name in seen_fields:
            raise LLMExtractionError(f"Duplicate field_name: {name}")
        seen_fields.add(name)

        status = field.get("field_status")
        if status not in valid_statuses:
            raise LLMExtractionError(
                f"Invalid field_status for {name}: {status}. Must be one of {valid_statuses}"
            )

        confidence = field.get("confidence")
        if confidence not in valid_confidences:
            raise LLMExtractionError(
                f"Invalid confidence for {name}: {confidence}. Must be one of {valid_confidences}"
            )

        # MISSING fields must have null value
        if status == "MISSING" and field.get("field_value") is not None:
            raise LLMExtractionError(
                f"Field {name} is MISSING but has a non-null value"
            )

        # FOUND/INFERRED fields must have a value
        if status in ("FOUND", "INFERRED") and not field.get("field_value"):
            raise LLMExtractionError(
                f"Field {name} is {status} but has no value"
            )

    # All 6 fields must be present (prompt requires this)
    missing_fields = valid_field_names - seen_fields
    if missing_fields:
        raise LLMExtractionError(f"LLM response missing fields: {missing_fields}")

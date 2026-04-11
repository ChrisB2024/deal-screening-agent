"""PII scrubbing utility for deal document text before sending to external LLM.

Security invariant (from spec): "No deal data is sent without PII scrubbing."
Trust boundary: API → OpenAI is Semi-trusted — we scrub before crossing.

This is a best-effort regex-based scrubber for MVP. Production should use
a dedicated NER-based PII detection service.
"""

import re

# Patterns to scrub — each tuple is (pattern, replacement)
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # US Social Security Numbers
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL_REDACTED]"),
    # US phone numbers (various formats)
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE_REDACTED]"),
    # Credit card numbers (basic pattern — 13-19 digits with optional separators)
    (re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{1,7}\b"), "[CC_REDACTED]"),
    # Individual names following common patterns like "Contact: John Smith"
    # Only scrub explicitly labeled personal contacts, not company/deal names
    (
        re.compile(
            r"(?:contact|prepared by|authored by|from|attention|attn)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)",
            re.IGNORECASE,
        ),
        lambda m: m.group(0).replace(m.group(1), "[NAME_REDACTED]"),
    ),
]


def scrub_pii(text: str) -> str:
    """Remove personally identifiable information from document text.

    Purpose: Sanitize deal document text before sending to external LLM.
    Inputs: Raw text extracted from a deal document.
    Outputs: Text with PII patterns replaced by redaction markers.
    Invariants: Always returns a string (never raises). Redaction markers
                are clearly identifiable so extraction can note them.
    Security: This is a defense-in-depth measure. Deal documents primarily
              contain business data, but may include personal contact info.
    """
    result = text
    for pattern, replacement in _PII_PATTERNS:
        if callable(replacement):
            result = pattern.sub(replacement, result)
        else:
            result = pattern.sub(replacement, result)
    return result

"""Input validation types — reason codes, failure results, validated file handles."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class ReasonCode(str, enum.Enum):
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    HEADER_TOO_LARGE = "HEADER_TOO_LARGE"
    TOO_MANY_PARTS = "TOO_MANY_PARTS"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    MIME_MAGIC_MISMATCH = "MIME_MAGIC_MISMATCH"
    MALFORMED_PDF = "MALFORMED_PDF"
    PDF_CONTAINS_JAVASCRIPT = "PDF_CONTAINS_JAVASCRIPT"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    PDF_TOO_MANY_PAGES = "PDF_TOO_MANY_PAGES"
    DECOMPRESSION_BOMB = "DECOMPRESSION_BOMB"
    COMPRESSION_RATIO_EXCEEDED = "COMPRESSION_RATIO_EXCEEDED"
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    WEBHOOK_MALFORMED = "WEBHOOK_MALFORMED"


REASON_HTTP_STATUS: dict[ReasonCode, int] = {
    ReasonCode.BODY_TOO_LARGE: 413,
    ReasonCode.HEADER_TOO_LARGE: 431,
    ReasonCode.TOO_MANY_PARTS: 413,
    ReasonCode.UNSUPPORTED_CONTENT_TYPE: 415,
    ReasonCode.MIME_MAGIC_MISMATCH: 415,
    ReasonCode.MALFORMED_PDF: 422,
    ReasonCode.PDF_CONTAINS_JAVASCRIPT: 422,
    ReasonCode.PDF_ENCRYPTED: 422,
    ReasonCode.PDF_TOO_MANY_PAGES: 422,
    ReasonCode.DECOMPRESSION_BOMB: 422,
    ReasonCode.COMPRESSION_RATIO_EXCEEDED: 422,
    ReasonCode.SCHEMA_VIOLATION: 400,
    ReasonCode.WEBHOOK_MALFORMED: 400,
}

REASON_USER_MESSAGE: dict[ReasonCode, str] = {
    ReasonCode.BODY_TOO_LARGE: "File is too large. Maximum is 50 MB.",
    ReasonCode.HEADER_TOO_LARGE: "Request headers are too large.",
    ReasonCode.TOO_MANY_PARTS: "Too many parts in the upload.",
    ReasonCode.UNSUPPORTED_CONTENT_TYPE: "We only accept PDF files right now.",
    ReasonCode.MIME_MAGIC_MISMATCH: "This doesn't appear to be a valid PDF.",
    ReasonCode.MALFORMED_PDF: "We couldn't read this PDF.",
    ReasonCode.PDF_CONTAINS_JAVASCRIPT: "This PDF contains content we can't process safely.",
    ReasonCode.PDF_ENCRYPTED: "Encrypted PDFs aren't supported. Please upload an unlocked copy.",
    ReasonCode.PDF_TOO_MANY_PAGES: "This PDF has too many pages. Maximum is 500.",
    ReasonCode.DECOMPRESSION_BOMB: "This file couldn't be safely processed.",
    ReasonCode.COMPRESSION_RATIO_EXCEEDED: "This file couldn't be safely processed.",
    ReasonCode.SCHEMA_VIOLATION: "Invalid request body.",
    ReasonCode.WEBHOOK_MALFORMED: "Malformed webhook payload.",
}


@dataclass(frozen=True)
class ValidationFailure:
    reason: ReasonCode
    detail: str | None = None

    @property
    def http_status(self) -> int:
        return REASON_HTTP_STATUS[self.reason]

    @property
    def user_message(self) -> str:
        return REASON_USER_MESSAGE[self.reason]


@dataclass(frozen=True)
class ValidatedFile:
    content: bytes
    size: int
    declared_mime: str
    detected_mime: str
    page_count: int | None = None

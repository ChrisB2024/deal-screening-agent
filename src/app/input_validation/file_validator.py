"""File validation — magic bytes and size checks."""

from __future__ import annotations

from .types import ReasonCode, ValidatedFile, ValidationFailure

MAGIC_BYTES: dict[str, list[bytes]] = {
    "application/pdf": [b"%PDF-"],
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_PDF_PAGES = 500


def check_magic_bytes(content: bytes, declared_mime: str) -> str | None:
    """Return detected MIME if magic matches, None if no match found."""
    for mime, signatures in MAGIC_BYTES.items():
        for sig in signatures:
            if content[:len(sig)] == sig:
                return mime
    return None


def validate_file(
    content: bytes,
    declared_mime: str | None,
    max_bytes: int = MAX_FILE_SIZE_BYTES,
) -> ValidatedFile | ValidationFailure:
    if len(content) > max_bytes:
        return ValidationFailure(
            reason=ReasonCode.BODY_TOO_LARGE,
            detail=f"File size {len(content)} exceeds limit {max_bytes}",
        )

    effective_mime = (declared_mime or "").lower().strip()

    if effective_mime not in MAGIC_BYTES:
        return ValidationFailure(
            reason=ReasonCode.UNSUPPORTED_CONTENT_TYPE,
            detail=f"Content type '{effective_mime}' is not accepted",
        )

    detected = check_magic_bytes(content, effective_mime)
    if detected is None or detected != effective_mime:
        return ValidationFailure(
            reason=ReasonCode.MIME_MAGIC_MISMATCH,
            detail=f"Magic bytes don't match declared type '{effective_mime}'",
        )

    return ValidatedFile(
        content=content,
        size=len(content),
        declared_mime=effective_mime,
        detected_mime=detected,
    )

"""Input Validation module — trusted gatekeeper for untrusted bytes.

Public API:
- validate_upload: magic bytes + size + PDF structural checks
- ValidationFailure / ValidatedFile: typed results
- ReasonCode: closed enum of rejection reasons
- InputValidationMiddleware: body size enforcement middleware
"""

from .file_validator import validate_file
from .middleware import InputValidationMiddleware
from .pdf_validator import validate_pdf
from .types import ReasonCode, ValidatedFile, ValidationFailure

__all__ = [
    "InputValidationMiddleware",
    "ReasonCode",
    "ValidatedFile",
    "ValidationFailure",
    "validate_file",
    "validate_pdf",
]


def validate_upload(
    content: bytes,
    declared_mime: str | None,
    max_bytes: int = 50 * 1024 * 1024,
) -> ValidatedFile | ValidationFailure:
    """Full upload validation: size → magic bytes → PDF structural checks."""
    result = validate_file(content, declared_mime, max_bytes=max_bytes)
    if isinstance(result, ValidationFailure):
        return result

    if result.detected_mime == "application/pdf":
        return validate_pdf(result)

    return result

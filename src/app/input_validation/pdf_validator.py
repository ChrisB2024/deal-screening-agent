"""PDF structural validation — checks for dangerous or unsupported features.

Portfolio phase: runs in-process via pypdf. Production hardening will move
this to a sandboxed subprocess with rlimit + seccomp.
"""

from __future__ import annotations

import io

from .file_validator import MAX_PDF_PAGES
from .types import ReasonCode, ValidatedFile, ValidationFailure

DANGEROUS_KEYS = {"/JavaScript", "/JS", "/Launch", "/EmbeddedFile"}
SCRIPT_ACTION_KEYS = {"/JavaScript", "/JS"}


def validate_pdf(validated_file: ValidatedFile) -> ValidatedFile | ValidationFailure:
    """Run structural checks on a validated PDF file.

    Checks: parseable, not encrypted, no JavaScript/Launch/EmbeddedFile actions,
    page count within bounds.
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(validated_file.content))
    except Exception as exc:
        return ValidationFailure(
            reason=ReasonCode.MALFORMED_PDF,
            detail=str(exc),
        )

    if reader.is_encrypted:
        return ValidationFailure(reason=ReasonCode.PDF_ENCRYPTED)

    page_count = len(reader.pages)
    if page_count > MAX_PDF_PAGES:
        return ValidationFailure(
            reason=ReasonCode.PDF_TOO_MANY_PAGES,
            detail=f"{page_count} pages exceeds limit of {MAX_PDF_PAGES}",
        )

    if _has_dangerous_actions(reader):
        return ValidationFailure(reason=ReasonCode.PDF_CONTAINS_JAVASCRIPT)

    return ValidatedFile(
        content=validated_file.content,
        size=validated_file.size,
        declared_mime=validated_file.declared_mime,
        detected_mime=validated_file.detected_mime,
        page_count=page_count,
    )


def _has_dangerous_actions(reader) -> bool:
    """Walk the PDF catalog and page trees looking for dangerous action types."""
    try:
        catalog = reader.trailer.get("/Root")
        if catalog is None:
            return False
        catalog_obj = catalog.get_object() if hasattr(catalog, "get_object") else catalog

        if _check_dict_for_keys(catalog_obj, DANGEROUS_KEYS):
            return True

        open_action = catalog_obj.get("/OpenAction")
        if open_action is not None:
            action_obj = open_action.get_object() if hasattr(open_action, "get_object") else open_action
            if isinstance(action_obj, dict) and _check_dict_for_keys(action_obj, SCRIPT_ACTION_KEYS):
                return True

        names = catalog_obj.get("/Names")
        if names is not None:
            names_obj = names.get_object() if hasattr(names, "get_object") else names
            if isinstance(names_obj, dict):
                if "/JavaScript" in names_obj or "/EmbeddedFiles" in names_obj:
                    return True

        for page in reader.pages:
            aa = page.get("/AA")
            if aa is not None:
                aa_obj = aa.get_object() if hasattr(aa, "get_object") else aa
                if isinstance(aa_obj, dict):
                    for trigger in aa_obj.values():
                        t = trigger.get_object() if hasattr(trigger, "get_object") else trigger
                        if isinstance(t, dict) and _check_dict_for_keys(t, SCRIPT_ACTION_KEYS):
                            return True

    except Exception:
        pass
    return False


def _check_dict_for_keys(d: dict, keys: set[str]) -> bool:
    if not isinstance(d, dict):
        return False
    for key in keys:
        if key in d:
            return True
    return False

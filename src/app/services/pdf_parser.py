"""PDF text extraction utility.

Extracts raw text from uploaded PDF documents for downstream LLM processing.
Handles failure modes: empty PDFs, image-only PDFs, corrupted files.
"""

import logging
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Max pages to process — caps cost and latency for very large CIMs
MAX_PAGES = 80
# Minimum text length to consider extraction successful
MIN_TEXT_LENGTH = 50


class PDFParseError(Exception):
    """Raised when a PDF cannot be parsed into usable text."""

    pass


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text content from a PDF file.

    Purpose: Convert uploaded PDF to raw text for LLM extraction.
    Inputs: Absolute path to a PDF file on disk.
    Outputs: Concatenated text from all pages (up to MAX_PAGES).
    Invariants: Never returns empty string without raising — caller always
                gets usable text or a clear error.
    Security: File path is trusted (comes from our storage layer, not user input).

    Raises:
        PDFParseError: If the file is unreadable, empty, or image-only.
        FileNotFoundError: If the file doesn't exist.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")

    if not path.suffix.lower() == ".pdf":
        raise PDFParseError(f"Not a PDF file: {path.suffix}")

    try:
        reader = PdfReader(str(path))
    except Exception as e:
        raise PDFParseError(f"Failed to read PDF: {e}") from e

    if len(reader.pages) == 0:
        raise PDFParseError("PDF has no pages")

    pages_to_read = min(len(reader.pages), MAX_PAGES)
    text_parts: list[str] = []

    for i in range(pages_to_read):
        try:
            page_text = reader.pages[i].extract_text() or ""
            text_parts.append(page_text)
        except Exception as e:
            logger.warning(f"Failed to extract text from page {i + 1}: {e}")
            continue

    full_text = "\n\n".join(text_parts).strip()

    if len(full_text) < MIN_TEXT_LENGTH:
        raise PDFParseError(
            f"Extracted text too short ({len(full_text)} chars). "
            "Document may be image-only or corrupted."
        )

    if pages_to_read < len(reader.pages):
        logger.info(
            f"Truncated PDF from {len(reader.pages)} to {pages_to_read} pages"
        )

    return full_text

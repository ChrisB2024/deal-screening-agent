from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import pdf_parser
from app.services.pdf_parser import PDFParseError, extract_text_from_pdf


class _FakePage:
    def __init__(self, text: str | None = None, error: Exception | None = None):
        self._text = text
        self._error = error

    def extract_text(self) -> str | None:
        if self._error is not None:
            raise self._error
        return self._text


def test_extract_text_from_pdf_rejects_missing_file(tmp_path: Path):
    missing = tmp_path / "missing.pdf"

    with pytest.raises(FileNotFoundError):
        extract_text_from_pdf(str(missing))


def test_extract_text_from_pdf_rejects_non_pdf_file(tmp_path: Path):
    not_pdf = tmp_path / "deal.txt"
    not_pdf.write_text("not a pdf")

    with pytest.raises(PDFParseError, match="Not a PDF file"):
        extract_text_from_pdf(str(not_pdf))


def test_extract_text_from_pdf_returns_joined_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "deal.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    fake_reader = SimpleNamespace(
        pages=[
            _FakePage("Revenue is 5000000"),
            _FakePage("EBITDA is 1200000 and geography is US Southeast"),
        ]
    )
    monkeypatch.setattr(pdf_parser, "PdfReader", lambda _: fake_reader)

    text = extract_text_from_pdf(str(pdf_path))

    assert "Revenue is 5000000" in text
    assert "US Southeast" in text


def test_extract_text_from_pdf_rejects_effectively_empty_or_image_only_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    pdf_path = tmp_path / "image_only.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    fake_reader = SimpleNamespace(pages=[_FakePage(""), _FakePage(None)])
    monkeypatch.setattr(pdf_parser, "PdfReader", lambda _: fake_reader)

    with pytest.raises(PDFParseError, match="Extracted text too short"):
        extract_text_from_pdf(str(pdf_path))


def test_extract_text_from_pdf_raises_for_zero_page_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    pdf_path = tmp_path / "empty.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    fake_reader = SimpleNamespace(pages=[])
    monkeypatch.setattr(pdf_parser, "PdfReader", lambda _: fake_reader)

    with pytest.raises(PDFParseError, match="PDF has no pages"):
        extract_text_from_pdf(str(pdf_path))

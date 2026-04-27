"""Tests for the PDF parser using a generated single-page PDF fixture."""

import pytest

from atlas_knowledge.parsers.pdf import parse_pdf


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Generate a minimal PDF in-memory with PyMuPDF — no external file needed."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello PDF World.\n\nSecond paragraph.")
    out = doc.tobytes()
    doc.close()
    return out


def test_parse_pdf_extracts_text(sample_pdf_bytes):
    doc = parse_pdf(sample_pdf_bytes, source_filename="hello.pdf")
    assert "Hello PDF World" in doc.text
    assert doc.title == "hello.pdf"
    assert doc.source_type == "pdf"


def test_parse_pdf_uses_filename_if_no_pdf_metadata_title(sample_pdf_bytes):
    doc = parse_pdf(sample_pdf_bytes, source_filename="report-Q3.pdf")
    assert doc.title == "report-Q3.pdf"


def test_parse_pdf_no_filename_falls_back(sample_pdf_bytes):
    doc = parse_pdf(sample_pdf_bytes)
    assert doc.title == "Untitled PDF"

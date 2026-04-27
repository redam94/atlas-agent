"""PDF parser using PyMuPDF (``fitz``)."""
import fitz

from atlas_knowledge.parsers.markdown import ParsedDocument


def parse_pdf(data: bytes, *, source_filename: str | None = None) -> ParsedDocument:
    """Extract text from a PDF byte buffer. Joins page text with double newlines."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        pages: list[str] = []
        for page in doc:
            pages.append(page.get_text("text"))
        text = "\n\n".join(pages).strip()
        # PDF metadata title is rarely useful; prefer filename, then fallback.
        meta_title = (doc.metadata or {}).get("title") or ""
        title = source_filename or (meta_title.strip() if meta_title.strip() else "Untitled PDF")
        return ParsedDocument(
            text=text,
            title=title,
            source_type="pdf",
            metadata={"page_count": doc.page_count},
        )
    finally:
        doc.close()

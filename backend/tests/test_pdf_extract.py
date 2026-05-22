"""PDF text extraction — synthesized PDFs via pypdf round-trip."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import RectangleObject

from app.services.pdf_extract import (
    extract_pdf_text,
    is_pdf_content_type,
)


def _make_pdf(pages: list[str]) -> bytes:
    """Build a minimal multi-page PDF with the given page texts."""
    writer = PdfWriter()
    for text in pages:
        # PdfWriter.add_blank_page makes the page but not the text. The
        # easiest portable way to embed text via pypdf is through the
        # AnnotationBuilder + a text annotation, but extract_text() doesn't
        # always see annotations. Use the pypdf 5.x add_page_with_text
        # path: build a page from raw PDF text operators.
        from pypdf.generic import (
            ArrayObject,
            ContentStream,
            DecodedStreamObject,
            DictionaryObject,
            FloatObject,
            NameObject,
            NumberObject,
            TextStringObject,
        )
        from pypdf import PageObject

        page = PageObject.create_blank_page(width=612, height=792)
        # PDF content stream: BT/ET text block with a Helvetica font.
        content = (
            "BT\n"
            "/F1 12 Tf\n"
            "72 720 Td\n"
            f"({text}) Tj\n"
            "ET\n"
        )
        stream = DecodedStreamObject()
        stream.set_data(content.encode("latin-1"))
        page[NameObject("/Contents")] = stream
        # Minimal font resource so the content stream is renderable.
        font_dict = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_dict}
                ),
            }
        )
        writer.add_page(page)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------- content-type sniffing ----------


@pytest.mark.parametrize(
    "ct,expected",
    [
        ("application/pdf", True),
        ("APPLICATION/PDF", True),
        ("application/pdf; charset=binary", True),
        ("text/html", False),
        ("application/xhtml+xml", False),
        (None, False),
        ("", False),
    ],
)
def test_is_pdf_content_type(ct: str | None, expected: bool):
    assert is_pdf_content_type(ct) is expected


# ---------- text extraction ----------


def test_extract_pdf_text_single_page():
    pdf = _make_pdf(["Contact Jane Doe at jane@school.edu"])
    text = extract_pdf_text(pdf)
    assert text is not None
    assert "jane@school.edu" in text


def test_extract_pdf_text_multi_page_joined_with_blank_line():
    pdf = _make_pdf(["Page one text", "Page two text"])
    text = extract_pdf_text(pdf)
    assert text is not None
    assert "Page one text" in text
    assert "Page two text" in text
    # Pages separated by a blank line so paragraph-level pre-pass logic
    # can treat them as distinct contexts.
    assert "\n\n" in text


def test_extract_pdf_text_empty_bytes_returns_none():
    assert extract_pdf_text(b"") is None


def test_extract_pdf_text_garbage_returns_none():
    assert extract_pdf_text(b"this is not a pdf, just bytes") is None


def test_extract_pdf_text_strips_page_whitespace():
    pdf = _make_pdf(["   "])
    # A page with only whitespace contributes nothing; if every page is
    # empty, the whole document returns None (signals to the caller that
    # OCR fallback would be the next step).
    assert extract_pdf_text(pdf) is None


# ---------- integration with crawl._extract_markdown ----------


def test_extract_markdown_routes_pdf_through_pdf_extractor():
    """A response with content-type application/pdf must go through the
    PDF extractor, not trafilatura (which would return None for PDF bytes)."""
    from app.workers.crawl import _extract_markdown

    pdf_bytes = _make_pdf(["Board approved trip; contact a@b.org"])
    out = _extract_markdown(pdf_bytes, "application/pdf")
    assert out is not None
    assert "a@b.org" in out


def test_extract_markdown_html_still_works():
    """Regression guard — HTML path must keep working after the PDF branch."""
    from app.workers.crawl import _extract_markdown

    html = b"<html><body><p>hello jane@example.org for tours</p></body></html>"
    out = _extract_markdown(html, "text/html")
    assert out is not None
    assert "jane@example.org" in out

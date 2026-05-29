"""OCR + candidate-image heuristics for image-obfuscated contact info."""

from __future__ import annotations

from app.services.image_ocr import (
    find_contact_image_candidates,
    ocr_image_bytes,
    ocr_is_available,
)


def test_finds_image_with_email_in_alt_text():
    html = '<img src="/staff/jane.png" alt="email Jane Doe">'
    candidates = find_contact_image_candidates(html, base_url="https://school.example/")
    assert candidates == ["https://school.example/staff/jane.png"]


def test_finds_image_with_contact_in_filename():
    html = '<img src="/img/contact-card.png" alt="">'
    candidates = find_contact_image_candidates(html, base_url="https://x.example/")
    assert candidates == ["https://x.example/img/contact-card.png"]


def test_ignores_image_without_contact_hint():
    html = '<img src="/img/hero.jpg" alt="campus aerial">'
    assert find_contact_image_candidates(html, base_url="https://x.example/") == []


def test_skips_logos_and_icons_even_with_contact_in_class():
    html = '<img src="/icons/email-sprite.svg" class="contact-icon">'
    # Filename matches the blocklist (sprite + icon) — drop it even though
    # the class says "contact".
    assert find_contact_image_candidates(html, base_url="https://x.example/") == []


def test_caps_at_three_candidates():
    html = "".join(
        f'<img src="/staff/p{i}.png" alt="email teacher {i}">' for i in range(10)
    )
    candidates = find_contact_image_candidates(html, base_url="https://x.example/")
    assert len(candidates) == 3


def test_dedupes_repeated_src():
    html = (
        '<img src="/staff/dir.png" alt="email Jane">'
        '<img src="/staff/dir.png" alt="contact Jane again">'
    )
    candidates = find_contact_image_candidates(html, base_url="https://x.example/")
    assert candidates == ["https://x.example/staff/dir.png"]


def test_empty_or_form_only_html_returns_no_candidates():
    assert find_contact_image_candidates("", base_url="https://x.example/") == []
    assert find_contact_image_candidates("<p>no images</p>", base_url="https://x.example/") == []
    assert find_contact_image_candidates(None) == []


def test_ocr_image_bytes_returns_empty_when_unavailable():
    """The OCR path MUST never raise — if tesseract isn't installed, every
    crawl page would crash. Empty bytes also returns empty string."""
    assert ocr_image_bytes(b"") == ""
    if not ocr_is_available():
        # In CI/containers without tesseract installed, any input should
        # silently return "".
        assert ocr_image_bytes(b"\x89PNG\r\n\x1a\nfake") == ""

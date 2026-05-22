"""URL discovery — links only, same-domain only, no mailto/tel/js, dedup."""

from __future__ import annotations

from app.services.url_discovery import discover_same_domain_urls


HTML = """
<html><body>
  <a href="/faculty/jane">Jane</a>
  <a href="https://phoenixisd.example.org/faculty/carlos">Carlos</a>
  <a href="https://other-domain.example.com/away">Should not include</a>
  <a href="mailto:a@b.com">email</a>
  <a href="tel:5125550100">tel</a>
  <a href="javascript:alert(1)">js</a>
  <a href="/faculty/jane">dup</a>
  <a href="/files/handbook.pdf">pdf (skip)</a>
  <a href="#section">anchor only</a>
  <a href="/page#section">fragment stripped</a>
</body></html>
"""


def test_discovery_same_domain_only() -> None:
    out = discover_same_domain_urls(
        base_url="https://phoenixisd.example.org/", html=HTML
    )
    # The expected absolute URLs, in source order.
    assert "https://phoenixisd.example.org/faculty/jane" in out
    assert "https://phoenixisd.example.org/faculty/carlos" in out
    assert "https://phoenixisd.example.org/page" in out


def test_discovery_excludes_other_domain() -> None:
    out = discover_same_domain_urls(
        base_url="https://phoenixisd.example.org/", html=HTML
    )
    assert all("other-domain" not in u for u in out)


def test_discovery_skips_non_http_schemes() -> None:
    out = discover_same_domain_urls(
        base_url="https://phoenixisd.example.org/", html=HTML
    )
    assert all(not u.startswith(("mailto:", "tel:", "javascript:")) for u in out)


def test_discovery_keeps_pdf_links() -> None:
    """PDFs are first-class crawl targets now (board minutes, faculty
    rosters). Other binary assets (images, css, archives) stay excluded."""
    out = discover_same_domain_urls(
        base_url="https://phoenixisd.example.org/", html=HTML
    )
    assert any(u.endswith(".pdf") for u in out)


def test_discovery_still_skips_binary_assets() -> None:
    html = (
        '<a href="/img.png">img</a> '
        '<a href="/style.css">css</a> '
        '<a href="/page">page</a>'
    )
    out = discover_same_domain_urls(base_url="https://x.test/", html=html)
    assert all(not u.endswith((".png", ".css")) for u in out)
    assert "https://x.test/page" in out


def test_discovery_dedupes_and_strips_fragments() -> None:
    out = discover_same_domain_urls(
        base_url="https://phoenixisd.example.org/", html=HTML
    )
    # /faculty/jane appears twice in HTML — once in output
    assert sum(1 for u in out if u.endswith("/faculty/jane")) == 1
    # #section should be stripped before dedupe
    assert "https://phoenixisd.example.org/page#section" not in out


def test_discovery_respects_max_urls() -> None:
    html = "".join(f'<a href="/p/{i}">L</a>' for i in range(50))
    out = discover_same_domain_urls(
        base_url="https://x.com/", html=html, max_urls=10
    )
    assert len(out) == 10

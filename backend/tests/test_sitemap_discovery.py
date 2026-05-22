"""Sitemap-based URL discovery — parsing + HTTP probe behavior."""

from __future__ import annotations

import httpx
import pytest

from app.services.sitemap_discovery import (
    _parse_robots_for_sitemaps,
    discover_sitemap_urls,
    parse_sitemap_xml,
)


# ---------- XML parsing ----------


SITEMAP_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def test_parse_urlset_returns_page_urls():
    xml = (
        f"<urlset {SITEMAP_NS}>"
        "<url><loc>https://example.org/a</loc></url>"
        "<url><loc>https://example.org/b</loc></url>"
        "</urlset>"
    )
    pages, children = parse_sitemap_xml(xml)
    assert pages == ["https://example.org/a", "https://example.org/b"]
    assert children == []


def test_parse_sitemapindex_returns_children():
    xml = (
        f"<sitemapindex {SITEMAP_NS}>"
        "<sitemap><loc>https://example.org/sm1.xml</loc></sitemap>"
        "<sitemap><loc>https://example.org/sm2.xml</loc></sitemap>"
        "</sitemapindex>"
    )
    pages, children = parse_sitemap_xml(xml)
    assert pages == []
    assert children == ["https://example.org/sm1.xml", "https://example.org/sm2.xml"]


def test_parse_tolerates_missing_xmlns():
    """Some sites publish sitemaps without the sitemap.org namespace; we
    fall back to a namespace-less scan."""
    xml = (
        "<urlset>"
        "<url><loc>https://example.org/a</loc></url>"
        "</urlset>"
    )
    pages, _ = parse_sitemap_xml(xml)
    assert pages == ["https://example.org/a"]


def test_parse_malformed_returns_empty():
    pages, children = parse_sitemap_xml("<not-real-xml")
    assert pages == [] and children == []


def test_parse_unknown_root_returns_empty():
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry/></feed>'
    pages, children = parse_sitemap_xml(xml)
    assert pages == [] and children == []


# ---------- robots.txt sitemap line extraction ----------


def test_robots_sitemap_extraction():
    robots = (
        "User-Agent: *\n"
        "Disallow: /admin\n"
        "Sitemap: https://example.org/sitemap.xml\n"
        "Sitemap: https://example.org/news_sitemap.xml\n"
        "# comment\n"
    )
    sms = _parse_robots_for_sitemaps(robots)
    assert sms == [
        "https://example.org/sitemap.xml",
        "https://example.org/news_sitemap.xml",
    ]


def test_robots_without_sitemap_is_empty():
    assert _parse_robots_for_sitemaps("User-Agent: *\nDisallow: /") == []


def test_robots_sitemap_extraction_case_insensitive():
    assert _parse_robots_for_sitemaps("SITEMAP: https://x.org/sm.xml") == [
        "https://x.org/sm.xml"
    ]


# ---------- end-to-end discover ----------


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def test_discover_uses_robots_sitemap_lines():
    """Robots.txt declares a sitemap; we follow it and surface its URLs."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="Sitemap: https://x.test/special.xml\n")
        if request.url.path == "/special.xml":
            return httpx.Response(
                200,
                text=(
                    f"<urlset {SITEMAP_NS}>"
                    "<url><loc>https://x.test/staff</loc></url>"
                    "<url><loc>https://x.test/faculty</loc></url>"
                    "</urlset>"
                ),
            )
        # Anything else (sitemap.xml, sitemap_index.xml) → 404
        return httpx.Response(404)

    out = discover_sitemap_urls("https://x.test/", client=_client(handler))
    assert "https://x.test/staff" in out
    assert "https://x.test/faculty" in out


def test_discover_falls_back_to_conventional_sitemap():
    """No robots.txt; we still try /sitemap.xml at the root."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                text=(
                    f"<urlset {SITEMAP_NS}>"
                    "<url><loc>https://x.test/contact</loc></url>"
                    "</urlset>"
                ),
            )
        return httpx.Response(404)

    out = discover_sitemap_urls("https://x.test/", client=_client(handler))
    assert out == ["https://x.test/contact"]


def test_discover_follows_sitemap_index_to_children():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                text=(
                    f"<sitemapindex {SITEMAP_NS}>"
                    "<sitemap><loc>https://x.test/child1.xml</loc></sitemap>"
                    "<sitemap><loc>https://x.test/child2.xml</loc></sitemap>"
                    "</sitemapindex>"
                ),
            )
        if request.url.path == "/child1.xml":
            return httpx.Response(
                200,
                text=(
                    f"<urlset {SITEMAP_NS}>"
                    "<url><loc>https://x.test/staff/1</loc></url>"
                    "</urlset>"
                ),
            )
        if request.url.path == "/child2.xml":
            return httpx.Response(
                200,
                text=(
                    f"<urlset {SITEMAP_NS}>"
                    "<url><loc>https://x.test/staff/2</loc></url>"
                    "</urlset>"
                ),
            )
        return httpx.Response(404)

    out = discover_sitemap_urls("https://x.test/", client=_client(handler))
    assert "https://x.test/staff/1" in out
    assert "https://x.test/staff/2" in out


def test_discover_drops_cross_domain_urls():
    """Sitemap-declared cross-domain entries are dropped; the link crawl
    handles external follow separately and intentionally."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                text=(
                    f"<urlset {SITEMAP_NS}>"
                    "<url><loc>https://x.test/keep</loc></url>"
                    "<url><loc>https://other.test/drop</loc></url>"
                    "</urlset>"
                ),
            )
        return httpx.Response(404)

    out = discover_sitemap_urls("https://x.test/", client=_client(handler))
    assert out == ["https://x.test/keep"]


def test_discover_respects_max_urls():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            urls = "".join(
                f"<url><loc>https://x.test/p/{i}</loc></url>" for i in range(50)
            )
            return httpx.Response(200, text=f"<urlset {SITEMAP_NS}>{urls}</urlset>")
        return httpx.Response(404)

    out = discover_sitemap_urls("https://x.test/", client=_client(handler), max_urls=5)
    assert len(out) == 5


def test_discover_returns_empty_when_nothing_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    assert discover_sitemap_urls("https://x.test/", client=_client(handler)) == []


def test_discover_caps_sitemap_fetches():
    """Pathological sitemap-index loop must terminate."""
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                text=(
                    f'<sitemapindex {SITEMAP_NS}>'
                    + "".join(
                        f"<sitemap><loc>https://x.test/sm/{i}.xml</loc></sitemap>"
                        for i in range(100)
                    )
                    + "</sitemapindex>"
                ),
            )
        # Each child sitemap is itself an index → would recurse forever
        # without the fetch cap.
        return httpx.Response(
            200,
            text=(
                f"<sitemapindex {SITEMAP_NS}>"
                "<sitemap><loc>https://x.test/sm/deep.xml</loc></sitemap>"
                "</sitemapindex>"
            ),
        )

    discover_sitemap_urls(
        "https://x.test/", client=_client(handler), max_sitemap_fetches=5
    )
    # 1 robots + at most 4 more fetches before the cap; never explodes.
    assert counter["n"] <= 5

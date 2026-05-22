"""Pagination URL detection — query-string and path-component patterns."""

from __future__ import annotations

import string

import pytest

from app.services.pagination_detect import (
    discover_pagination_urls,
    synthesize_az_pagination,
)


# ---------- discover_pagination_urls ----------


def test_discover_finds_page_query_links():
    html = """
    <html><body>
      <a href="?page=2">Page 2</a>
      <a href="?page=3">Page 3</a>
      <a href="/about">About</a>
    </body></html>
    """
    out = discover_pagination_urls("https://x.test/staff", html)
    assert "https://x.test/staff?page=2" in out
    assert "https://x.test/staff?page=3" in out
    assert all("/about" not in u for u in out)


def test_discover_finds_letter_query_links():
    html = """
    <html><body>
      <a href="?letter=A">A</a>
      <a href="?letter=B">B</a>
      <a href="?letter=C">C</a>
    </body></html>
    """
    out = discover_pagination_urls("https://x.test/staff", html)
    assert "https://x.test/staff?letter=A" in out
    assert "https://x.test/staff?letter=C" in out


def test_discover_finds_path_pagination():
    html = '<a href="/staff/page/2">2</a> <a href="/staff/page/3">3</a>'
    out = discover_pagination_urls("https://x.test/staff", html)
    assert "https://x.test/staff/page/2" in out
    assert "https://x.test/staff/page/3" in out


def test_discover_finds_path_letter_pagination():
    html = '<a href="/staff/A">A</a> <a href="/faculty/letter/B">B</a>'
    out = discover_pagination_urls("https://x.test/staff", html)
    assert "https://x.test/staff/A" in out
    assert "https://x.test/faculty/letter/B" in out


def test_discover_ignores_off_domain():
    html = '<a href="https://other.test/staff?page=2">2</a>'
    out = discover_pagination_urls("https://x.test/staff", html)
    assert out == []


def test_discover_ignores_non_pagination_query():
    html = '<a href="?sort=name">sort</a> <a href="?filter=teacher">filter</a>'
    out = discover_pagination_urls("https://x.test/staff", html)
    assert out == []


def test_discover_dedupes_and_strips_fragments():
    html = (
        '<a href="?page=2">2</a> '
        '<a href="?page=2#top">2 again</a> '
        '<a href="?page=2">dup</a>'
    )
    out = discover_pagination_urls("https://x.test/staff", html)
    assert out.count("https://x.test/staff?page=2") == 1


def test_discover_skips_mailto_tel_js_anchor():
    html = (
        '<a href="mailto:x@y">x</a> '
        '<a href="tel:5125550100">phone</a> '
        '<a href="javascript:void(0)">js</a> '
        '<a href="#top">anchor</a> '
        '<a href="?page=2">real</a>'
    )
    out = discover_pagination_urls("https://x.test/staff", html)
    assert out == ["https://x.test/staff?page=2"]


def test_discover_respects_max_urls():
    links = "".join(f'<a href="?page={i}">{i}</a>' for i in range(100))
    out = discover_pagination_urls(
        "https://x.test/staff", f"<html><body>{links}</body></html>", max_urls=5
    )
    assert len(out) == 5


# ---------- synthesize_az_pagination ----------


def test_synthesize_az_returns_full_alphabet():
    out = synthesize_az_pagination("https://x.test/staff/")
    assert len(out) == 26
    # First should be ?letter=A; last ?letter=Z; order matches A..Z.
    assert out[0] == "https://x.test/staff/?letter=A"
    assert out[-1] == "https://x.test/staff/?letter=Z"


def test_synthesize_az_preserves_existing_query():
    out = synthesize_az_pagination("https://x.test/staff?sort=last")
    assert out[0] == "https://x.test/staff?sort=last&letter=A"


def test_synthesize_az_handles_empty_url():
    assert synthesize_az_pagination("/relative/path") == []

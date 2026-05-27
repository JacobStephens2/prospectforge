"""SERP Tier-0 — query construction + provider integration."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.services import serp_search
from app.services.serp_search import (
    _build_freesearch_query,
    _build_query,
    _filter_same_domain,
    find_person_page,
    find_person_via_freesearch,
)


# ---------- query construction ----------


def test_build_query_quotes_name_and_uses_site_op():
    q = _build_query("Jane Smith", "school.edu")
    assert q == 'site:school.edu "Jane Smith"'


def test_build_query_strips_www_and_scheme():
    q = _build_query("Jane Smith", "https://www.school.edu/some/path")
    assert q == 'site:school.edu "Jane Smith"'


def test_build_query_trims_whitespace_in_name():
    q = _build_query("  Jane   Smith ", "school.edu")
    assert q == 'site:school.edu "Jane   Smith"'


# ---------- same-domain filter ----------


def test_filter_same_domain_keeps_exact_host():
    urls = [
        "https://school.edu/staff/jane",
        "https://www.school.edu/staff/bob",
        "https://other.com/about",
    ]
    out = _filter_same_domain(urls, "school.edu")
    assert "https://school.edu/staff/jane" in out
    assert "https://www.school.edu/staff/bob" in out
    assert all("other.com" not in u for u in out)


def test_filter_same_domain_keeps_subdomain():
    urls = [
        "https://teachers.school.edu/jane",
        "https://random-cdn.com/cached/jane",
    ]
    out = _filter_same_domain(urls, "school.edu")
    assert "https://teachers.school.edu/jane" in out
    assert len(out) == 1


# ---------- find_person_page integration ----------


def test_returns_none_when_no_provider_configured():
    """Default state — no provider set, function is a no-op."""
    with patch.object(serp_search.settings, "serp_provider", ""):
        assert find_person_page("Jane Smith", "school.edu") is None


def test_returns_none_for_empty_name_or_domain():
    with patch.object(serp_search.settings, "serp_provider", "brave"):
        assert find_person_page("", "school.edu") is None
        assert find_person_page("Jane", "") is None


def test_unknown_provider_logs_and_returns_none(caplog):
    with patch.object(serp_search.settings, "serp_provider", "bing-stub"):
        assert find_person_page("Jane Smith", "school.edu") is None


def test_brave_returns_first_same_domain_hit():
    mock_response = httpx.Response(
        200,
        json={
            "web": {
                "results": [
                    {"url": "https://other.cdn/cached/jane"},
                    {"url": "https://school.edu/staff/jane"},
                    {"url": "https://school.edu/news/2024/jane"},
                ]
            }
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "site:school.edu" in request.url.query.decode() or "site%3Aschool.edu" in request.url.query.decode()
        return mock_response

    with patch.object(serp_search.settings, "serp_provider", "brave"), \
         patch.object(serp_search.settings, "serp_brave_api_key", "fake-key"), \
         patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = mock_response
        out = find_person_page("Jane Smith", "school.edu")

    assert out == "https://school.edu/staff/jane"


def test_brave_returns_none_when_only_off_domain_hits():
    mock_response = httpx.Response(
        200,
        json={"web": {"results": [{"url": "https://other.cdn/x"}]}},
    )

    with patch.object(serp_search.settings, "serp_provider", "brave"), \
         patch.object(serp_search.settings, "serp_brave_api_key", "fake-key"), \
         patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = mock_response
        assert find_person_page("Jane Smith", "school.edu") is None


def test_brave_http_error_returns_none():
    """A SERP outage must not break enrichment."""
    with patch.object(serp_search.settings, "serp_provider", "brave"), \
         patch.object(serp_search.settings, "serp_brave_api_key", "fake-key"), \
         patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.side_effect = httpx.ConnectError("network down")
        assert find_person_page("Jane Smith", "school.edu") is None


def test_brave_http_4xx_returns_none():
    with patch.object(serp_search.settings, "serp_provider", "brave"), \
         patch.object(serp_search.settings, "serp_brave_api_key", "fake-key"), \
         patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = httpx.Response(429, text="rate limited")
        assert find_person_page("Jane Smith", "school.edu") is None


def test_build_freesearch_query_quotes_both():
    """Free-form query phrase-quotes both name and org for disambiguation."""
    q = _build_freesearch_query("Patricia Muumba", "Sunrise School District")
    assert q == '"Patricia Muumba" "Sunrise School District"'


def test_find_person_via_freesearch_returns_top_hit():
    mock_response = httpx.Response(
        200,
        json={
            "web": {
                "results": [
                    {"url": "https://sunrisesd.org/staff/muumba"},
                    {"url": "https://linkedin.com/in/patricia-muumba"},
                ]
            }
        },
    )
    with patch.object(serp_search.settings, "serp_provider", "brave"), \
         patch.object(serp_search.settings, "serp_brave_api_key", "fake-key"), \
         patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = mock_response
        out = find_person_via_freesearch("Patricia Muumba", "Sunrise School District")
    # First result wins — caller decides whether to crawl off-domain pages.
    assert out == "https://sunrisesd.org/staff/muumba"


def test_find_person_via_freesearch_returns_none_when_no_provider():
    with patch.object(serp_search.settings, "serp_provider", ""):
        assert (
            find_person_via_freesearch("Patricia Muumba", "Sunrise School District")
            is None
        )


def test_find_person_via_freesearch_requires_org_name():
    with patch.object(serp_search.settings, "serp_provider", "brave"), \
         patch.object(serp_search.settings, "serp_brave_api_key", "fake-key"):
        assert find_person_via_freesearch("Patricia Muumba", "") is None
        assert find_person_via_freesearch("", "Sunrise SD") is None


def test_find_person_via_freesearch_returns_none_when_no_results():
    mock_response = httpx.Response(200, json={"web": {"results": []}})
    with patch.object(serp_search.settings, "serp_provider", "brave"), \
         patch.object(serp_search.settings, "serp_brave_api_key", "fake-key"), \
         patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = mock_response
        assert (
            find_person_via_freesearch("Patricia Muumba", "Sunrise School District")
            is None
        )


def test_serpapi_path():
    """SerpAPI shape uses organic_results instead of web.results."""
    mock_response = httpx.Response(
        200,
        json={
            "organic_results": [
                {"link": "https://school.edu/staff/jane"},
            ]
        },
    )
    with patch.object(serp_search.settings, "serp_provider", "serpapi"), \
         patch.object(serp_search.settings, "serp_serpapi_api_key", "fake-key"), \
         patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = mock_response
        out = find_person_page("Jane Smith", "school.edu")
    assert out == "https://school.edu/staff/jane"

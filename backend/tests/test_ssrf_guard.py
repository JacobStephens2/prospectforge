"""SSRF guard tests — cover the evasion cases the spec explicitly calls out
plus the routine private-IP rejections we never want to regress on."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ssrf_guard import SSRFBlocked, validate_target_url


def _resolve(*ips: str):
    """Helper: patch DNS resolution to return the given IPs."""
    return patch(
        "app.services.ssrf_guard._resolve_all",
        return_value=ips,
    )


# ---------- public URLs pass ----------


def test_public_ipv4_passes() -> None:
    with _resolve("142.250.190.78"):  # public google.com IP
        r = validate_target_url("https://example.com/foo")
    assert r.host == "example.com"
    assert r.scheme == "https"
    assert r.port == 443
    assert "142.250.190.78" in r.resolved_ips


def test_public_with_explicit_port() -> None:
    with _resolve("142.250.190.78"):
        r = validate_target_url("https://example.com:8443/foo")
    assert r.port == 8443


# ---------- scheme enforcement ----------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
        "data:text/html,<h1>x</h1>",
        "javascript:alert(1)",
        "about:blank",
    ],
)
def test_non_http_schemes_blocked(url: str) -> None:
    with pytest.raises(SSRFBlocked) as exc:
        validate_target_url(url)
    assert exc.value.code == "bad_scheme"


# ---------- private IP rejection ----------


@pytest.mark.parametrize(
    "ip,family",
    [
        ("10.0.0.5", "private 10/8"),
        ("172.16.4.4", "private 172.16/12"),
        ("192.168.1.1", "private 192.168/16"),
        ("127.0.0.1", "loopback"),
        ("169.254.169.254", "cloud metadata"),
        ("169.254.0.1", "link-local"),
        ("0.0.0.0", "unspecified"),
        ("224.0.0.1", "multicast"),
    ],
)
def test_private_ranges_blocked(ip: str, family: str) -> None:
    """Both via DNS resolution and via IP literal."""
    with _resolve(ip):
        with pytest.raises(SSRFBlocked) as exc:
            validate_target_url("https://attacker.example.com/")
    assert exc.value.code == "blocked_ip", f"{family} should block via DNS"

    with pytest.raises(SSRFBlocked) as exc:
        validate_target_url(f"https://{ip}/")
    assert exc.value.code == "blocked_ip", f"{family} should block via IP literal"


def test_ipv6_loopback_blocked() -> None:
    with pytest.raises(SSRFBlocked) as exc:
        validate_target_url("https://[::1]/")
    assert exc.value.code == "blocked_ip"


# ---------- Acme internal IP denylist ----------


def test_eta_internal_ip_blocked_via_dns() -> None:
    """An attacker can't reach the CRM DB by pointing a domain at it."""
    with _resolve("203.0.113.10"):  # crm-db.acme-travel.example
        with pytest.raises(SSRFBlocked) as exc:
            validate_target_url("https://attacker.example.com/")
    assert exc.value.code == "blocked_ip"
    assert "203.0.113.10" in exc.value.detail


# ---------- DNS rebinding ----------


def test_dns_rebinding_caught_on_revalidation() -> None:
    """A hostname that resolves to a public IP on the first call and a private
    IP on the second must be rejected on the second call. The caller is
    expected to re-validate after each redirect (see spec §6.3)."""
    public = "142.250.190.78"
    private = "10.0.0.5"

    with _resolve(public):
        validate_target_url("https://chameleon.example.com/")  # passes

    with _resolve(private):
        with pytest.raises(SSRFBlocked) as exc:
            validate_target_url("https://chameleon.example.com/")
    assert exc.value.code == "blocked_ip"


# ---------- multi-record DNS (a single bad record poisons the resolve) ----------


def test_any_resolved_ip_blocks_the_whole_target() -> None:
    """If a hostname resolves to both a public and a private IP, we reject."""
    with _resolve("142.250.190.78", "10.0.0.5"):
        with pytest.raises(SSRFBlocked) as exc:
            validate_target_url("https://mixed.example.com/")
    assert exc.value.code == "blocked_ip"


# ---------- malformed inputs ----------


def test_no_host_rejected() -> None:
    with pytest.raises(SSRFBlocked) as exc:
        validate_target_url("https:///nopath")
    assert exc.value.code == "no_host"


def test_unresolvable_host_rejected() -> None:
    with _resolve():  # empty tuple — getaddrinfo failure
        with pytest.raises(SSRFBlocked) as exc:
            validate_target_url("https://nx.example.invalid/")
    assert exc.value.code == "dns_fail"

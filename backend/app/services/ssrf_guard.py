"""SSRF protection — non-negotiable per spec §6.3.

Morgan's UX lets her paste any URL, which makes this a primary attack surface.
Every fetch (and every redirect target) goes through `validate_target_url()`
before a network round-trip. Failure raises `SSRFBlocked` with a code that
explains why; the caller logs to source_pages.metadata and aborts.

What this enforces:
- Scheme is exactly http or https. file://, ftp://, gopher://, data:, etc. blocked.
- DNS resolves to a publicly-routable IP. Private ranges (RFC 1918), loopback,
  link-local, multicast, reserved, and the cloud metadata IP 169.254.169.254
  are all blocked.
- Configurable explicit denylist for Acme internal IPs (CRM DB, prod web,
  file server). Sourced from PF_SSRF_INTERNAL_IP_DENYLIST.
- Validation is **per-target**, not per-hostname. After every redirect the
  caller re-validates the new URL — that closes DNS rebinding, where DNS for
  attacker.example.com flips between a public IP (on first lookup) and a
  private IP (on the second lookup during the actual fetch).

What this does NOT do:
- Time-of-check vs time-of-use racing at the kernel: we resolve in Python and
  then httpx resolves again at connect time. To close that gap fully we'd
  need to pin the socket to the resolved IP via a custom Transport. Tracked
  as a v1.1 follow-up; see tests/test_ssrf_guard.py::test_dns_rebinding for
  the property we ARE catching today (changed answers across calls).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import settings


class SSRFBlocked(Exception):
    """Raised when a URL fails SSRF validation. `code` is stable for log keys."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ResolvedTarget:
    """Result of a successful validation: parsed URL pieces + resolved IPs."""

    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: tuple[str, ...]


# IPv6 reserved ranges we explicitly reject. ipaddress.ip_address(...).is_private
# catches most, but the metadata IPv6 address and unique-local addresses warrant
# spelling out for log clarity.
_IPV6_METADATA = ipaddress.IPv6Address("fd00:ec2::254")

# 169.254.169.254 is the IMDS endpoint on AWS, GCP, DO, Azure, Linode, etc.
_CLOUD_METADATA_V4 = ipaddress.IPv4Address("169.254.169.254")


def _load_denylist() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    raw = (settings.pf_ssrf_internal_ip_denylist or "").strip()
    if not raw:
        return ()
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.append(ipaddress.ip_network(piece, strict=False))
        except ValueError:
            # A malformed entry is a config bug worth surfacing loudly.
            raise SSRFBlocked("bad_denylist", f"unparseable PF_SSRF_INTERNAL_IP_DENYLIST entry: {piece!r}")
    return tuple(out)


def _ip_is_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    denylist: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> tuple[bool, str]:
    """Return (blocked, reason). Reason is empty when not blocked."""
    if ip == _CLOUD_METADATA_V4:
        return True, "cloud metadata endpoint (169.254.169.254)"
    if isinstance(ip, ipaddress.IPv6Address) and ip == _IPV6_METADATA:
        return True, "IPv6 cloud metadata endpoint"
    if ip.is_loopback:
        return True, f"loopback ({ip})"
    if ip.is_link_local:
        return True, f"link-local ({ip})"
    if ip.is_multicast:
        return True, f"multicast ({ip})"
    if ip.is_private:
        return True, f"private ({ip})"
    if ip.is_reserved:
        return True, f"reserved ({ip})"
    if ip.is_unspecified:
        return True, f"unspecified ({ip})"
    for net in denylist:
        if ip in net:
            return True, f"denylist {net} matched {ip}"
    return False, ""


def _resolve_all(host: str) -> tuple[str, ...]:
    """All IPs DNS returns for host. Empty tuple on resolution failure."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return ()
    seen: dict[str, None] = {}
    for family, _type, _proto, _canonname, sockaddr in infos:
        # sockaddr[0] is the IP literal for both AF_INET and AF_INET6.
        if isinstance(sockaddr, tuple) and sockaddr:
            seen[sockaddr[0]] = None
    return tuple(seen.keys())


def validate_target_url(url: str) -> ResolvedTarget:
    """Validate a URL before fetching. Raises SSRFBlocked on any failure.

    Called at the start of each fetch AND after each redirect — the redirect
    target gets the same scrutiny as the original URL.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise SSRFBlocked("bad_scheme", f"scheme {scheme!r} not allowed; only http/https")

    host = parsed.hostname
    if not host:
        raise SSRFBlocked("no_host", f"URL has no hostname: {url!r}")

    # IP literals: skip DNS, validate the literal directly.
    literal_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    denylist = _load_denylist()

    if literal_ip is not None:
        blocked, reason = _ip_is_blocked(literal_ip, denylist)
        if blocked:
            raise SSRFBlocked("blocked_ip", reason)
        resolved = (str(literal_ip),)
    else:
        resolved = _resolve_all(host)
        if not resolved:
            raise SSRFBlocked("dns_fail", f"could not resolve {host!r}")
        for ip_str in resolved:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                raise SSRFBlocked("bad_resolved", f"DNS returned non-IP {ip_str!r} for {host!r}")
            blocked, reason = _ip_is_blocked(ip, denylist)
            if blocked:
                raise SSRFBlocked("blocked_ip", f"{host} → {reason}")

    port = parsed.port or (443 if scheme == "https" else 80)
    return ResolvedTarget(url=url, scheme=scheme, host=host, port=port, resolved_ips=resolved)

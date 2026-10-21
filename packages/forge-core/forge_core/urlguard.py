"""SSRF-resistant validation for user-supplied Git repository URLs.

Threat: a user registers a repository URL that points at loopback, RFC1918 space, or a
cloud metadata endpoint, and Forge's checkout step becomes a confused deputy.

Controls implemented here:

* scheme allowlist (``https`` only by default)
* rejection of embedded credentials in the URL
* rejection of literal IP hosts in disallowed ranges
* DNS resolution of the hostname, with **every** resolved address checked
* re-validation at connection time via :func:`assert_resolved_addresses_allowed`,
  which callers invoke immediately before connecting to defeat DNS rebinding
* an optional host allowlist for locked-down deployments

Limitation: a pure userspace check cannot fully close the TOCTOU window between
resolution and connect. The checkout path additionally pins the resolved address
(see ``forge_worker.handlers.pipeline``) and the documented production control is a
network-level egress policy. This is stated in ``docs/security.md``.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from forge_core.errors import UnsafeUrlError

ALLOWED_SCHEMES: frozenset[str] = frozenset({"https"})

#: Networks Forge must never originate outbound checkout traffic to.
_BLOCKED_V4 = (
    ipaddress.ip_network("0.0.0.0/8"),        # "this network"
    ipaddress.ip_network("10.0.0.0/8"),       # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("169.254.0.0/16"),   # link-local incl. 169.254.169.254 metadata
    ipaddress.ip_network("172.16.0.0/12"),    # RFC1918
    ipaddress.ip_network("192.0.0.0/24"),     # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),     # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),   # RFC1918
    ipaddress.ip_network("198.18.0.0/15"),    # benchmarking
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),   # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),      # multicast
    ipaddress.ip_network("240.0.0.0/4"),      # reserved
)
_BLOCKED_V6 = (
    ipaddress.ip_network("::/128"),           # unspecified
    ipaddress.ip_network("::1/128"),          # loopback
    ipaddress.ip_network("fc00::/7"),         # unique local
    ipaddress.ip_network("fe80::/10"),        # link-local
    ipaddress.ip_network("ff00::/8"),         # multicast
    ipaddress.ip_network("2001:db8::/32"),    # documentation
)

#: Hostnames that resolve to infrastructure endpoints regardless of DNS answer.
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {"localhost", "metadata.google.internal", "metadata.goog", "instance-data"}
)


def is_blocked_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True when ``addr`` is in a range Forge refuses to connect to."""
    if isinstance(addr, ipaddress.IPv6Address):
        # An IPv4-mapped IPv6 address must be judged by its embedded IPv4 value.
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return is_blocked_address(mapped)
        if addr.sixtofour is not None:
            return is_blocked_address(addr.sixtofour)
        return any(addr in net for net in _BLOCKED_V6)
    return any(addr in net for net in _BLOCKED_V4)


@dataclass(frozen=True, slots=True)
class ValidatedRepoUrl:
    """A repository URL that passed static validation, with its resolved addresses."""

    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...] = field(default=())


def _resolve(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(
            "Repository host could not be resolved.", details={"host": host}
        ) from exc
    addresses = tuple(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise UnsafeUrlError("Repository host resolved to no addresses.",
                             details={"host": host})
    return addresses


def assert_resolved_addresses_allowed(addresses: tuple[str, ...], host: str) -> None:
    """Raise unless *every* address is outside the blocked ranges.

    Call this again immediately before connecting; re-checking is what makes DNS
    rebinding between validation and connect detectable.
    """
    for raw in addresses:
        # getaddrinfo may return a scoped IPv6 literal such as "fe80::1%en0".
        addr = ipaddress.ip_address(raw.split("%", 1)[0])
        if is_blocked_address(addr):
            raise UnsafeUrlError(
                "Repository host resolves to a disallowed network.",
                details={"host": host, "address": str(addr)},
            )


def validate_repo_url(
    raw_url: str,
    *,
    resolve: bool = True,
    allowed_hosts: frozenset[str] | None = None,
) -> ValidatedRepoUrl:
    """Validate a user-supplied repository URL.

    Args:
        raw_url: the candidate URL.
        resolve: when True, resolve DNS and check every resulting address.
        allowed_hosts: when provided, the host must be a member (exact, lowercased).

    Raises:
        UnsafeUrlError: if the URL is malformed or targets a disallowed destination.
    """
    candidate = (raw_url or "").strip()
    if not candidate:
        raise UnsafeUrlError("Repository URL must not be empty.")
    if len(candidate) > 2048:
        raise UnsafeUrlError("Repository URL is too long.")
    if any(ch in candidate for ch in ("\n", "\r", "\t", " ")):
        raise UnsafeUrlError("Repository URL must not contain whitespace.")

    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"Unsupported URL scheme {scheme or '(none)'!r}. "
            f"Allowed: {', '.join(sorted(ALLOWED_SCHEMES))}.",
            details={"scheme": scheme},
        )
    if parts.username or parts.password or "@" in parts.netloc:
        raise UnsafeUrlError("Repository URL must not embed credentials.")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeUrlError("Repository URL has no host.")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost") or host.endswith(".local"):
        raise UnsafeUrlError("Repository host is not permitted.", details={"host": host})
    if allowed_hosts is not None and host not in allowed_hosts:
        raise UnsafeUrlError(
            "Repository host is not in the configured allowlist.", details={"host": host}
        )

    try:
        port = parts.port or 443
    except ValueError as exc:
        raise UnsafeUrlError("Repository URL has an invalid port.") from exc
    if not 1 <= port <= 65535:
        raise UnsafeUrlError("Repository URL has an invalid port.")

    # A literal IP host is checked directly; no DNS is involved.
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if is_blocked_address(literal):
            raise UnsafeUrlError(
                "Repository URL targets a disallowed network.", details={"host": host}
            )
        return ValidatedRepoUrl(candidate, scheme, host, port, (str(literal),))

    if not resolve:
        return ValidatedRepoUrl(candidate, scheme, host, port, ())

    addresses = _resolve(host, port)
    assert_resolved_addresses_allowed(addresses, host)
    return ValidatedRepoUrl(candidate, scheme, host, port, addresses)


__all__ = [
    "ALLOWED_SCHEMES",
    "ValidatedRepoUrl",
    "assert_resolved_addresses_allowed",
    "is_blocked_address",
    "validate_repo_url",
]

"""SSRF-resistant repository URL validation."""

from __future__ import annotations

import ipaddress

import pytest
from forge_core.errors import UnsafeUrlError
from forge_core.urlguard import (
    assert_resolved_addresses_allowed,
    is_blocked_address,
    validate_repo_url,
)


class TestSchemeAndFormat:
    @pytest.mark.parametrize(
        "url",
        [
            "http://github.com/a/b",
            "file:///etc/passwd",
            "ftp://host/x",
            "gopher://host/x",
            "git://github.com/a/b",
            "ssh://git@github.com/a/b",
            "git@github.com:a/b.git",
            "javascript:alert(1)",
            "",
        ],
    )
    def test_non_https_schemes_are_rejected(self, url: str) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_repo_url(url, resolve=False)

    def test_embedded_credentials_are_rejected(self) -> None:
        with pytest.raises(UnsafeUrlError, match="credentials"):
            validate_repo_url("https://user:pass@github.com/a/b", resolve=False)

    def test_whitespace_is_rejected(self) -> None:
        with pytest.raises(UnsafeUrlError, match="whitespace"):
            validate_repo_url("https://github.com/a b", resolve=False)

    def test_newline_injection_is_rejected(self) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_repo_url("https://github.com/a\r\nHost: evil", resolve=False)

    def test_overlong_url_is_rejected(self) -> None:
        with pytest.raises(UnsafeUrlError, match="too long"):
            validate_repo_url("https://github.com/" + "a" * 4000, resolve=False)

    def test_valid_https_url_is_accepted(self) -> None:
        result = validate_repo_url("https://github.com/psf/requests.git", resolve=False)
        assert result.host == "github.com"
        assert result.port == 443

    def test_explicit_port_is_preserved(self) -> None:
        result = validate_repo_url("https://git.example.com:8443/a/b", resolve=False)
        assert result.port == 8443

    def test_trailing_dot_host_is_normalised(self) -> None:
        """'github.com.' and 'github.com' resolve identically; treat them as one."""
        result = validate_repo_url("https://github.com./a/b", resolve=False)
        assert result.host == "github.com"


class TestBlockedDestinations:
    @pytest.mark.parametrize(
        "url",
        [
            "https://127.0.0.1/x",
            "https://127.1.2.3/x",
            "https://0.0.0.0/x",
            "https://10.0.0.1/x",
            "https://172.16.0.1/x",
            "https://192.168.1.1/x",
            "https://169.254.169.254/latest/meta-data/",
            "https://100.64.0.1/x",
            "https://[::1]/x",
            "https://[fc00::1]/x",
            "https://[fe80::1]/x",
            "https://224.0.0.1/x",
        ],
    )
    def test_literal_addresses_in_blocked_ranges_are_rejected(self, url: str) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_repo_url(url, resolve=False)

    @pytest.mark.parametrize(
        "url",
        [
            "https://localhost/x",
            "https://metadata.google.internal/x",
            "https://foo.localhost/x",
            "https://printer.local/x",
        ],
    )
    def test_blocked_hostnames_are_rejected(self, url: str) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_repo_url(url, resolve=False)

    def test_ipv4_mapped_ipv6_loopback_is_rejected(self) -> None:
        """::ffff:127.0.0.1 is loopback wearing an IPv6 costume."""
        with pytest.raises(UnsafeUrlError):
            validate_repo_url("https://[::ffff:127.0.0.1]/x", resolve=False)

    def test_6to4_encapsulated_private_address_is_rejected(self) -> None:
        assert is_blocked_address(ipaddress.ip_address("2002:0a00:0001::"))

    def test_public_address_is_allowed(self) -> None:
        assert not is_blocked_address(ipaddress.ip_address("140.82.121.3"))
        assert not is_blocked_address(ipaddress.ip_address("2606:4700::1111"))


class TestResolvedAddressChecking:
    def test_resolved_private_address_is_rejected(self) -> None:
        """This is the DNS-rebinding defence: the answer is checked, not the name."""
        with pytest.raises(UnsafeUrlError, match="disallowed network"):
            assert_resolved_addresses_allowed(("10.0.0.5",), "evil.example.com")

    def test_any_blocked_address_in_the_set_rejects(self) -> None:
        """A hostname resolving to both public and private addresses is unsafe."""
        with pytest.raises(UnsafeUrlError):
            assert_resolved_addresses_allowed(("140.82.121.3", "127.0.0.1"), "mixed.example.com")

    def test_all_public_addresses_pass(self) -> None:
        assert_resolved_addresses_allowed(("140.82.121.3", "140.82.121.4"), "github.com")

    def test_scoped_ipv6_literal_is_parsed(self) -> None:
        with pytest.raises(UnsafeUrlError):
            assert_resolved_addresses_allowed(("fe80::1%en0",), "host")


class TestHostAllowlist:
    def test_host_outside_allowlist_is_rejected(self) -> None:
        with pytest.raises(UnsafeUrlError, match="allowlist"):
            validate_repo_url(
                "https://evil.com/a/b",
                resolve=False,
                allowed_hosts=frozenset({"github.com"}),
            )

    def test_host_inside_allowlist_is_accepted(self) -> None:
        result = validate_repo_url(
            "https://github.com/a/b",
            resolve=False,
            allowed_hosts=frozenset({"github.com"}),
        )
        assert result.host == "github.com"

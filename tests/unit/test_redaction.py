"""Secret redaction."""

from __future__ import annotations

import pytest
from forge_core.redaction import (
    MASK,
    is_sensitive_key,
    redact_mapping,
    redact_object,
    redact_text,
)


@pytest.mark.parametrize(
    "raw",
    [
        "https://user:hunter2@github.com/a/b",
        "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE",
        "password = 'hunter2'",
        "api_key: sk-abcdef123456",
        "Authorization: Bearer eyJhbGciOi.eyJzdWIi.c2lnbmF0dXJl",
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_credential_shaped_text_is_masked(raw: str) -> None:
    assert MASK in redact_text(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "Build succeeded in 42ms",
        "Running pytest -q tests",
        "commit 8d3febd62c17 on main",
        "",
    ],
)
def test_ordinary_text_is_untouched(raw: str) -> None:
    assert redact_text(raw) == raw


def test_url_keeps_structure_but_loses_the_password() -> None:
    result = redact_text("https://alice:s3cr3t@example.com/repo.git")
    assert "s3cr3t" not in result
    assert "alice" in result and "example.com" in result


def test_jwt_is_masked_entirely() -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijkl"
    assert token not in redact_text(f"token={token}")


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "API_KEY",
        "db_password",
        "Authorization",
        "webhook_secret",
        "DATABASE_URL",
        "session_token",
        "private_key",
    ],
)
def test_sensitive_keys_are_recognised(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["username", "commit_sha", "duration_ms", "version"])
def test_ordinary_keys_are_not_sensitive(key: str) -> None:
    assert not is_sensitive_key(key)


def test_mapping_values_for_sensitive_keys_are_masked() -> None:
    result = redact_mapping({"user": "alice", "password": "hunter2", "port": 8080})
    assert result == {"user": "alice", "password": MASK, "port": 8080}


def test_nested_structures_are_redacted_recursively() -> None:
    payload = {
        "outer": {"inner": {"api_key": "secret-value"}},
        "list": [{"token": "abc123def456"}],
        "safe": "ok",
    }
    result = redact_object(payload)
    assert result["outer"]["inner"]["api_key"] == MASK
    assert result["list"][0]["token"] == MASK
    assert result["safe"] == "ok"


def test_non_string_scalars_pass_through() -> None:
    assert redact_object(42) == 42
    assert redact_object(None) is None
    assert redact_object(True) is True

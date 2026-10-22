"""Secret redaction for logs, API errors and pipeline output.

Forge streams build logs produced by untrusted repository code and renders them in the
dashboard. Anything that looks like a credential is masked before it is persisted.

Redaction is best-effort defence in depth, not a guarantee: the primary control is that
secrets are never placed in the build environment in the first place
(see ``forge_worker.builders.local``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Final

MASK: Final = "[REDACTED]"

#: Environment/config keys whose values are always masked, matched case-insensitively
#: as a substring of the key.
SENSITIVE_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "apikey",
        "api_key",
        "access_key",
        "private_key",
        "credential",
        "authorization",
        "auth",
        "session",
        "cookie",
        "signature",
        "webhook_secret",
        "dsn",
        "database_url",
        "salt",
    }
)

_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # URL userinfo: scheme://user:pass@host -> scheme://user:[REDACTED]@host
    (
        re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/\s@]+):[^@/\s]+@"),
        rf"\g<scheme>\g<user>:{MASK}@",
    ),
    # Bearer / token headers
    (re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._\-=/+]{8,}"), rf"\1 {MASK}"),
    # key=value / key: value assignments for sensitive-looking names
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_.\-]*(?:password|passwd|secret|token|api[_-]?key|"
            r"access[_-]?key|credential|signature)[A-Za-z0-9_.\-]*)"
            r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;)]+)"
        ),
        rf"\1\2{MASK}",
    ),
    # PEM private key blocks
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
        MASK,
    ),
    # JWTs
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"), MASK),
    # GitHub-style tokens
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"), MASK),
    # AWS access key ids
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),
)


def redact_text(text: str) -> str:
    """Mask credential-shaped substrings in free text."""
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_value(key: str, value: Any) -> Any:
    """Mask ``value`` when ``key`` names a secret, else redact its text content."""
    if is_sensitive_key(key):
        return MASK
    return redact_object(value)


def redact_object(value: Any) -> Any:
    """Recursively redact a JSON-like structure."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_object(v) for v in value)
    return value


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    return {k: redact_value(k, v) for k, v in data.items()}


def redact_lines(lines: Iterable[str]) -> list[str]:
    return [redact_text(line) for line in lines]


__all__ = [
    "MASK",
    "is_sensitive_key",
    "redact_lines",
    "redact_mapping",
    "redact_object",
    "redact_text",
]

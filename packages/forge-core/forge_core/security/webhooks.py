"""GitHub webhook signature verification (``X-Hub-Signature-256``)."""

from __future__ import annotations

import hashlib
import hmac

from forge_core.errors import AuthenticationError

_PREFIX = "sha256="


def compute_signature(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return _PREFIX + digest


def verify_signature(payload: bytes, header_value: str | None, secret: str) -> None:
    """Verify a GitHub HMAC signature in constant time.

    Raises :class:`AuthenticationError` when the secret is unset, the header is absent
    or malformed, or the digest does not match. An unset secret is a hard failure:
    unsigned webhooks are never accepted.
    """
    if not secret:
        raise AuthenticationError(
            "Webhook ingestion is disabled because no webhook secret is configured."
        )
    if not header_value or not header_value.startswith(_PREFIX):
        raise AuthenticationError("Missing or malformed webhook signature header.")

    expected = compute_signature(payload, secret)
    if not hmac.compare_digest(expected, header_value):
        raise AuthenticationError("Webhook signature verification failed.")


__all__ = ["compute_signature", "verify_signature"]

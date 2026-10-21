"""Session tokens (JWT, HS256) and CSRF tokens.

Tokens are short-lived and carry a ``jti`` so individual sessions can be revoked via
the session table. Revocation is checked against PostgreSQL, not only the token body.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from forge_core.errors import AuthenticationError

_ALGORITHM = "HS256"
_ISSUER = "forge"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    jti: str
    role: str
    issued_at: datetime
    expires_at: datetime


def create_access_token(
    *, subject: str, role: str, secret: str, ttl_seconds: int, jti: str | None = None
) -> tuple[str, TokenClaims]:
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=ttl_seconds)
    token_id = jti or str(uuid.uuid4())
    payload: dict[str, Any] = {
        "sub": subject,
        "jti": token_id,
        "role": role,
        "iss": _ISSUER,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    encoded = jwt.encode(payload, secret, algorithm=_ALGORITHM)
    return encoded, TokenClaims(subject, token_id, role, now, expires)


def decode_access_token(token: str, *, secret: str) -> TokenClaims:
    """Decode and verify a token. Raises :class:`AuthenticationError` when invalid."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Session has expired.") from exc
    except jwt.InvalidTokenError as exc:
        # Deliberately opaque: the caller must not learn why validation failed.
        raise AuthenticationError("Invalid authentication token.") from exc

    return TokenClaims(
        subject=str(payload["sub"]),
        jti=str(payload["jti"]),
        role=str(payload.get("role", "VIEWER")),
        issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
    )


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


__all__ = [
    "TokenClaims",
    "constant_time_equals",
    "create_access_token",
    "decode_access_token",
    "generate_csrf_token",
]

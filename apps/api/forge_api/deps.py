"""Shared dependencies: database sessions, authentication and authorization.

Authorization is enforced here, on the server. The frontend hides controls a user
cannot use, but that is presentation only — every protected route depends on
:func:`require` and fails closed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from forge_core.authz import Permission, permissions_for, require_permission
from forge_core.config import Settings, get_settings
from forge_core.db.models import Session as SessionRow
from forge_core.db.models import User
from forge_core.db.session import get_sessionmaker
from forge_core.enums import Role
from forge_core.errors import AuthenticationError, RateLimitedError
from forge_core.security.tokens import constant_time_equals, decode_access_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SESSION_COOKIE = "forge_session"
CSRF_COOKIE = "forge_csrf"
CSRF_HEADER = "X-CSRF-Token"
#: Methods that mutate state and therefore require a CSRF token when the caller
#: authenticated with a cookie.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    session_id: uuid.UUID

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.role)

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


async def get_db() -> AsyncIterator[AsyncSession]:
    """One session per request.

    The commit here is a safety net, not the contract. Because the app installs
    BaseHTTPMiddleware, the response is streamed and this teardown can run *after*
    the client has already received the response — so a client that writes and then
    immediately reads could miss its own write. Every mutating route therefore
    commits explicitly before returning (see ``commit``).
    """
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def settings_dependency() -> Settings:
    return get_settings()


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(settings_dependency)]


def _extract_token(request: Request) -> tuple[str, bool]:
    """Return ``(token, came_from_cookie)``.

    Bearer tokens are accepted for API clients; the cookie is for the browser SPA.
    """
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), False
    cookie = request.cookies.get(SESSION_COOKIE, "")
    return cookie, bool(cookie)


async def current_user(
    request: Request, session: DbSession, settings: AppSettings
) -> CurrentUser:
    """Resolve the caller, verifying the token *and* the server-side session row."""
    token, from_cookie = _extract_token(request)
    if not token:
        raise AuthenticationError("Authentication is required for this endpoint.")

    claims = decode_access_token(token, secret=settings.secret_key.get_secret_value())

    row = (
        await session.execute(select(SessionRow).where(SessionRow.jti == claims.jti))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        # Revocation is checked against the database, not only the token body, so a
        # logout or an admin revocation takes effect immediately.
        raise AuthenticationError("This session is no longer valid.")
    if row.expires_at <= datetime.now(UTC):
        raise AuthenticationError("Session has expired.")

    # CSRF: only cookie-authenticated unsafe requests need the double-submit token.
    # Bearer callers are not subject to ambient-credential attacks.
    if (
        from_cookie
        and settings.csrf_protection_enabled
        and request.method in UNSAFE_METHODS
    ):
        supplied = request.headers.get(CSRF_HEADER, "")
        if not supplied or not row.csrf_token or not constant_time_equals(
            supplied, row.csrf_token
        ):
            raise AuthenticationError(
                "A valid CSRF token is required for cookie-authenticated writes."
            )

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("This account is not active.")

    return CurrentUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=Role(user.role),
        session_id=row.id,
    )


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]


def require(
    permission: Permission,
) -> Callable[[CurrentUser], Awaitable[CurrentUser]]:
    """Build a dependency that enforces ``permission`` for the current user."""

    async def _dependency(user: CurrentUserDep) -> CurrentUser:
        require_permission(user.role, permission)
        return user

    return _dependency


async def enforce_rate_limit(
    request: Request, key: str, *, limit: int, window_seconds: int = 60
) -> None:
    """Apply a rate limit, raising :class:`RateLimitedError` when exceeded."""
    limiter = getattr(request.app.state, "rate_limiter", None)
    settings: Settings = request.app.state.settings
    if limiter is None or not settings.rate_limit_enabled:
        return
    client = request.client.host if request.client else "unknown"
    decision = await limiter.check(
        f"{key}:{client}", limit=limit, window_seconds=window_seconds
    )
    if not decision.allowed:
        raise RateLimitedError(
            "Too many requests. Retry after the window resets.",
            details={"limit": decision.limit, "retry_after_seconds": decision.reset_seconds},
        )


async def commit(session: AsyncSession) -> None:
    """Flush and commit before a route returns.

    Mutating handlers must call this rather than relying on dependency teardown,
    so the write is durable before the client can act on the response.
    """
    await session.commit()


__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "SESSION_COOKIE",
    "AppSettings",
    "CurrentUser",
    "CurrentUserDep",
    "DbSession",
    "commit",
    "current_user",
    "enforce_rate_limit",
    "get_db",
    "require",
]

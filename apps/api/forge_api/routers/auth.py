"""Authentication: login, logout, current user."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response, status
from forge_core.db.events import record_audit
from forge_core.db.models import Session as SessionRow
from forge_core.db.models import User, normalize_email
from forge_core.errors import AuthenticationError
from forge_core.logging import get_logger
from forge_core.security.passwords import hash_password, needs_rehash, verify_password
from forge_core.security.tokens import create_access_token, generate_csrf_token
from sqlalchemy import select

from forge_api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AppSettings,
    CurrentUserDep,
    DbSession,
    enforce_rate_limit,
)
from forge_api.schemas import CurrentUserOut, LoginRequest, TokenResponse

logger = get_logger("forge.api.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

#: After this many consecutive failures the account is locked for a short period.
#: This bounds online password guessing independently of the IP-based rate limit.
MAX_FAILED_LOGINS = 10
LOCKOUT_MINUTES = 15


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> TokenResponse:
    await enforce_rate_limit(
        request, "login", limit=settings.rate_limit_login_per_minute
    )
    email = normalize_email(payload.email)
    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")[:256]

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if user is not None and user.locked_until and user.locked_until > now:
        await record_audit(
            session, action="login", actor_label=email, outcome="DENIED",
            resource_type="user", resource_id=str(user.id),
            detail={"reason": "account_locked"},
            ip_address=client_ip, user_agent=user_agent,
            request_id=getattr(request.state, "request_id", ""),
        )
        raise AuthenticationError(
            "This account is temporarily locked after repeated failed sign-ins."
        )

    # Verify unconditionally against a dummy hash when the user is absent, so the
    # response time does not reveal whether an address is registered.
    stored = user.password_hash if user is not None else _DUMMY_HASH
    valid = verify_password(payload.password, stored)

    if user is None or not valid or not user.is_active:
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                logger.warning("account_locked", user_id=str(user.id))
        await record_audit(
            session, action="login", actor_label=email, outcome="FAILURE",
            resource_type="user", resource_id=str(user.id) if user else "",
            detail={"reason": "invalid_credentials"},
            ip_address=client_ip, user_agent=user_agent,
            request_id=getattr(request.state, "request_id", ""),
        )
        # One message for every failure mode: no account enumeration.
        raise AuthenticationError("Incorrect email address or password.")

    # Opportunistic rehash so parameters can be raised without forcing a reset.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now

    csrf_token = generate_csrf_token()
    token, claims = create_access_token(
        subject=str(user.id),
        role=user.role,
        secret=settings.secret_key.get_secret_value(),
        ttl_seconds=settings.access_token_ttl_seconds,
    )
    session.add(
        SessionRow(
            id=uuid.uuid4(), user_id=user.id, jti=claims.jti,
            expires_at=claims.expires_at, csrf_token=csrf_token,
            user_agent=user_agent, ip_address=client_ip,
        )
    )
    await record_audit(
        session, action="login", actor_id=user.id, actor_label=user.email,
        outcome="SUCCESS", resource_type="user", resource_id=str(user.id),
        ip_address=client_ip, user_agent=user_agent,
        request_id=getattr(request.state, "request_id", ""),
    )

    secure = not settings.is_development
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=settings.access_token_ttl_seconds,
        httponly=True,       # not readable by JavaScript
        secure=secure,       # HTTPS-only outside development
        samesite="lax",      # blocks cross-site form posts
        path="/",
    )
    # The CSRF cookie is deliberately readable by the SPA so it can echo the value
    # in a header: that is the double-submit pattern.
    response.set_cookie(
        CSRF_COOKIE, csrf_token,
        max_age=settings.access_token_ttl_seconds,
        httponly=False, secure=secure, samesite="lax", path="/",
    )
    return TokenResponse(
        access_token=token, expires_at=claims.expires_at, csrf_token=csrf_token
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End session")
async def logout(
    request: Request,
    response: Response,
    session: DbSession,
    user: CurrentUserDep,
) -> Response:
    row = await session.get(SessionRow, user.session_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
    await record_audit(
        session, action="logout", actor_id=user.id, actor_label=user.email,
        resource_type="session", resource_id=str(user.session_id),
        request_id=getattr(request.state, "request_id", ""),
    )
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=CurrentUserOut, summary="Current user")
async def me(session: DbSession, user: CurrentUserDep) -> CurrentUserOut:
    row = await session.get(User, user.id)
    if row is None:  # pragma: no cover - resolved user always exists
        raise AuthenticationError("This account is no longer available.")
    return CurrentUserOut(
        id=row.id, email=row.email, display_name=row.display_name, role=row.role,
        is_active=row.is_active, last_login_at=row.last_login_at,
        created_at=row.created_at,
        permissions=sorted(str(p) for p in user.permissions),
    )


#: A fixed Argon2 hash of a value no one knows, used to equalise timing on the
#: unknown-account path. Not a credential.
_DUMMY_HASH = hash_password("forge-timing-equalisation-placeholder-value")

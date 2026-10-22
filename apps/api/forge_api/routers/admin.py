"""Administrative settings and user management."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from forge_core.authz import Permission
from forge_core.db.events import record_audit
from forge_core.db.models import User, normalize_email
from forge_core.enums import Role
from forge_core.errors import ConflictError, NotFoundError, ValidationError
from forge_core.security.passwords import PasswordPolicyError, hash_password
from sqlalchemy import select

from forge_api.deps import CurrentUserDep, DbSession, commit, require
from forge_api.schemas import CreateUserRequest, SettingsOut, UserOut

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/settings", response_model=SettingsOut, summary="Operational settings")
async def get_settings_view(
    request: Request,
    user: Annotated[CurrentUserDep, Depends(require(Permission.SETTINGS_READ))],
) -> SettingsOut:
    """Expose configuration to the dashboard.

    Secret *values* are never returned — only whether each one is configured.
    """
    settings = request.app.state.settings
    limiter = getattr(request.app.state, "rate_limiter", None)
    return SettingsOut(
        environment=settings.environment,
        version=settings.version,
        builder=settings.builder,
        registry=settings.registry,
        deploy_adapter=settings.deploy_adapter,
        canary_steps=list(settings.canary_steps),
        canary_step_seconds=settings.canary_step_seconds,
        health_min_samples=settings.health_min_samples,
        health_max_error_rate=settings.health_max_error_rate,
        health_max_p95_latency_ms=settings.health_max_p95_latency_ms,
        rate_limit_enabled=settings.rate_limit_enabled,
        webhook_secret_configured=bool(
            settings.github_webhook_secret.get_secret_value()
        ),
        redis_configured=bool(settings.redis_url),
        rate_limiter_backend=limiter.name if limiter else "disabled",
        router_admin_url=settings.router_admin_url,
        unsandboxed_builds_allowed=settings.allow_unsandboxed_builds,
    )


@router.get("/users", response_model=list[UserOut], summary="List users")
async def list_users(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.USER_MANAGE))],
) -> list[UserOut]:
    rows = (await session.execute(select(User).order_by(User.created_at))).scalars()
    return [UserOut.model_validate(u) for u in rows]


@router.post(
    "/users", response_model=UserOut, status_code=status.HTTP_201_CREATED,
    summary="Create a user",
)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.USER_MANAGE))],
) -> UserOut:
    email = normalize_email(payload.email)
    if "@" not in email:
        raise ValidationError("A valid email address is required.")
    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise ConflictError("A user with that email address already exists.")

    try:
        password_hash = hash_password(payload.password)
    except PasswordPolicyError as exc:
        raise ValidationError(str(exc)) from exc

    created = User(
        id=uuid.uuid4(),
        email=email,
        display_name=payload.display_name or email.split("@")[0],
        password_hash=password_hash,
        role=Role(payload.role).value,
        is_active=True,
    )
    session.add(created)
    await session.flush()
    await record_audit(
        session, action="user.create", actor_id=user.id, actor_label=user.email,
        resource_type="user", resource_id=str(created.id),
        detail={"email": email, "role": created.role},
        ip_address=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    await commit(session)
    return UserOut.model_validate(created)


@router.post(
    "/users/{user_id}/deactivate", response_model=UserOut, summary="Deactivate a user"
)
async def deactivate_user(
    user_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.USER_MANAGE))],
) -> UserOut:
    target = await session.get(User, user_id)
    if target is None:
        raise NotFoundError("No such user.")
    if target.id == user.id:
        # Removing your own access leaves the platform potentially unadministrable.
        raise ConflictError("You cannot deactivate your own account.")

    target.is_active = False
    await record_audit(
        session, action="user.deactivate", actor_id=user.id, actor_label=user.email,
        resource_type="user", resource_id=str(user_id),
        ip_address=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    await commit(session)
    return UserOut.model_validate(target)

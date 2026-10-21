"""Development-only administrator bootstrap and environment seeding.

The bootstrap administrator exists so a fresh local stack is usable immediately. It
is deliberately constrained:

* both the email and the password must be supplied via environment variables
* it is refused outside ``development``/``test`` (enforced in ``Settings``)
* there is no built-in default password anywhere in the codebase
* an existing user is never silently overwritten
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from forge_core.config import Settings
from forge_core.db.models import Environment, User, normalize_email
from forge_core.enums import Role
from forge_core.logging import get_logger
from forge_core.security.passwords import PasswordPolicyError, hash_password
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger("forge.bootstrap")

DEFAULT_ENVIRONMENTS: tuple[tuple[str, str, bool], ...] = (
    ("development", "Development", False),
    ("staging", "Staging", True),
)


async def ensure_environments(session: AsyncSession) -> int:
    """Create the default environments if they are absent. Idempotent."""
    created = 0
    for name, display, protected in DEFAULT_ENVIRONMENTS:
        exists = await session.scalar(
            select(Environment.id).where(Environment.name == name)
        )
        if exists is None:
            session.add(
                Environment(
                    id=uuid.uuid4(), name=name, display_name=display,
                    is_protected=protected, config={},
                )
            )
            created += 1
    return created


async def ensure_bootstrap_admin(session: AsyncSession, settings: Settings) -> bool:
    """Create the development administrator when configured. Returns True if created."""
    email = (settings.bootstrap_admin_email or "").strip()
    password = settings.bootstrap_admin_password.get_secret_value()
    if not email or not password:
        return False
    if not settings.is_development:
        # Settings already rejects this, but failing closed twice is cheap.
        logger.error("bootstrap_admin_refused_outside_development")
        return False

    normalized = normalize_email(email)
    existing = await session.scalar(select(User.id).where(User.email == normalized))
    if existing is not None:
        logger.info("bootstrap_admin_exists", email=normalized)
        return False

    try:
        password_hash = hash_password(password)
    except PasswordPolicyError as exc:
        logger.error("bootstrap_admin_password_rejected", reason=str(exc))
        return False

    session.add(
        User(
            id=uuid.uuid4(),
            email=normalized,
            display_name="Bootstrap Administrator",
            password_hash=password_hash,
            role=Role.ADMIN.value,
            is_active=True,
        )
    )
    logger.warning(
        "bootstrap_admin_created",
        email=normalized,
        detail="Development-only account created from FORGE_BOOTSTRAP_ADMIN_*.",
    )
    return True


async def user_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(User.id))) or 0)


async def run_bootstrap(session: AsyncSession, settings: Settings) -> dict[str, object]:
    environments = await ensure_environments(session)
    admin_created = await ensure_bootstrap_admin(session, settings)
    # Flush so the counts below reflect rows added in this transaction.
    await session.flush()
    total_users = await user_count(session)
    if total_users == 0:
        logger.warning(
            "no_users_configured",
            detail=(
                "No users exist. Set FORGE_BOOTSTRAP_ADMIN_EMAIL and "
                "FORGE_BOOTSTRAP_ADMIN_PASSWORD (development only) or create one "
                "with 'make create-user'."
            ),
        )
    return {
        "environments_created": environments,
        "admin_created": admin_created,
        "users": total_users,
        "at": datetime.now(UTC).isoformat(),
    }


__all__ = [
    "DEFAULT_ENVIRONMENTS",
    "ensure_bootstrap_admin",
    "ensure_environments",
    "run_bootstrap",
    "user_count",
]

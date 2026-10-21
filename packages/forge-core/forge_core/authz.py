"""Role-based authorization.

Permissions are enforced server-side on every route. The frontend hides controls the
user cannot use, but that is cosmetic only — the check that matters is here.
"""

from __future__ import annotations

from enum import StrEnum

from forge_core.enums import Role
from forge_core.errors import AuthorizationError


class Permission(StrEnum):
    REPO_READ = "repo:read"
    REPO_WRITE = "repo:write"
    REPO_DELETE = "repo:delete"
    PIPELINE_READ = "pipeline:read"
    PIPELINE_TRIGGER = "pipeline:trigger"
    PIPELINE_CANCEL = "pipeline:cancel"
    DEPLOYMENT_READ = "deployment:read"
    DEPLOYMENT_CREATE = "deployment:create"
    DEPLOYMENT_CANCEL = "deployment:cancel"
    DEPLOYMENT_ROLLBACK = "deployment:rollback"
    AUDIT_READ = "audit:read"
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"
    USER_MANAGE = "user:manage"


_VIEWER: frozenset[Permission] = frozenset(
    {
        Permission.REPO_READ,
        Permission.PIPELINE_READ,
        Permission.DEPLOYMENT_READ,
        Permission.SETTINGS_READ,
    }
)

_DEVELOPER: frozenset[Permission] = _VIEWER | frozenset(
    {
        Permission.REPO_WRITE,
        Permission.PIPELINE_TRIGGER,
        Permission.PIPELINE_CANCEL,
        Permission.DEPLOYMENT_CREATE,
        Permission.DEPLOYMENT_CANCEL,
        Permission.DEPLOYMENT_ROLLBACK,
        Permission.AUDIT_READ,
    }
)

_ADMIN: frozenset[Permission] = _DEVELOPER | frozenset(
    {
        Permission.REPO_DELETE,
        Permission.SETTINGS_WRITE,
        Permission.USER_MANAGE,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.DEVELOPER: _DEVELOPER,
    Role.ADMIN: _ADMIN,
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in permissions_for(role)


def require_permission(role: Role, permission: Permission) -> None:
    """Raise :class:`AuthorizationError` unless ``role`` grants ``permission``."""
    if not has_permission(role, permission):
        raise AuthorizationError(
            f"Role {role} does not permit {permission}.",
            details={"required_permission": str(permission)},
        )


__all__ = [
    "ROLE_PERMISSIONS",
    "Permission",
    "has_permission",
    "permissions_for",
    "require_permission",
]

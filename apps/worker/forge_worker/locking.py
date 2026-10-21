"""Deployment locking.

Two deployments must never mutate the same service+environment concurrently. The lock
is a row in PostgreSQL rather than a Redis key, because the thing it protects —
deployment state — is also in PostgreSQL, and a lock that can disagree with the state
it guards is not a lock.

Fencing: every acquisition bumps a monotonically increasing token. A holder whose lease
expired and whose lock was taken over cannot write, because its token is stale. This is
the standard defence against the "paused process wakes up and clobbers" failure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from forge_core.errors import FencedError, LockAcquisitionError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class DeploymentLock:
    resource: str
    owner: str
    token: int
    expires_at: datetime


def resource_key(service_id: uuid.UUID | str, environment_id: uuid.UUID | str) -> str:
    return f"deploy:{service_id}:{environment_id}"


async def acquire(
    session: AsyncSession, resource: str, owner: str, *, ttl_seconds: int = 900
) -> DeploymentLock:
    """Take the lock, or raise :class:`LockAcquisitionError` when held by someone else.

    An expired lock is reclaimable; the token increments either way so the previous
    holder is fenced out.
    """
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=ttl_seconds)
    result = await session.execute(
        text(
            """
            INSERT INTO deployment_locks (resource, owner, token, acquired_at, expires_at)
            VALUES (:resource, :owner, 1, :now, :expires)
            ON CONFLICT (resource) DO UPDATE
                SET owner = EXCLUDED.owner,
                    token = deployment_locks.token + 1,
                    acquired_at = EXCLUDED.acquired_at,
                    expires_at = EXCLUDED.expires_at
                WHERE deployment_locks.expires_at < :now
                   OR deployment_locks.owner = EXCLUDED.owner
            RETURNING token, expires_at
            """
        ),
        {"resource": resource, "owner": owner, "now": now, "expires": expires},
    )
    row = result.mappings().one_or_none()
    if row is None:
        held = await session.execute(
            text("SELECT owner, expires_at FROM deployment_locks WHERE resource = :r"),
            {"r": resource},
        )
        current = held.mappings().one_or_none()
        raise LockAcquisitionError(
            "Another deployment is already in progress for this service and "
            "environment.",
            details={
                "resource": resource,
                "held_by": current["owner"] if current else "unknown",
                "expires_at": current["expires_at"].isoformat() if current else "",
            },
        )
    return DeploymentLock(resource, owner, row["token"], row["expires_at"])


async def renew(
    session: AsyncSession, lock: DeploymentLock, *, ttl_seconds: int = 900
) -> DeploymentLock:
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=ttl_seconds)
    result = await session.execute(
        text(
            """
            UPDATE deployment_locks
            SET expires_at = :expires
            WHERE resource = :resource AND owner = :owner AND token = :token
            RETURNING token, expires_at
            """
        ),
        {"resource": lock.resource, "owner": lock.owner,
         "token": lock.token, "expires": expires},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise FencedError(
            "Deployment lock was taken over by another worker; this deployment must "
            "stop before it mutates shared infrastructure.",
            details={"resource": lock.resource, "token": lock.token},
        )
    return DeploymentLock(lock.resource, lock.owner, row["token"], row["expires_at"])


async def assert_held(session: AsyncSession, lock: DeploymentLock) -> None:
    """Raise unless this holder still owns the lock with the same token."""
    result = await session.execute(
        text("SELECT owner, token, expires_at FROM deployment_locks WHERE resource = :r"),
        {"r": lock.resource},
    )
    row = result.mappings().one_or_none()
    if row is None or row["owner"] != lock.owner or row["token"] != lock.token:
        raise FencedError(
            "Deployment lock is no longer held by this worker.",
            details={"resource": lock.resource, "expected_token": lock.token,
                     "actual_token": row["token"] if row else None},
        )


async def release(session: AsyncSession, lock: DeploymentLock) -> bool:
    result = await session.execute(
        text(
            """
            DELETE FROM deployment_locks
            WHERE resource = :resource AND owner = :owner AND token = :token
            """
        ),
        {"resource": lock.resource, "owner": lock.owner, "token": lock.token},
    )
    return (result.rowcount or 0) > 0


async def assert_held_in(sessionmaker: Any, lock: DeploymentLock) -> None:
    """Convenience wrapper: open a short session and assert the lock is still ours.

    Used at each canary step so a controller that lost its lease stops before it
    changes traffic weights.
    """
    async with sessionmaker() as session:
        await assert_held(session, lock)


__all__ = [
    "DeploymentLock",
    "acquire",
    "assert_held",
    "assert_held_in",
    "release",
    "renew",
    "resource_key",
]

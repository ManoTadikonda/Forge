"""Durable, PostgreSQL-backed job queue.

Why PostgreSQL rather than Redis: jobs drive deployments, so losing one loses track of
running infrastructure. The queue therefore lives in the same transactional store as
the state it mutates, and a job can be enqueued atomically with the row that justifies
it (``enqueue`` participates in the caller's transaction).

Delivery semantics are **at-least-once**. Exactly-once is not offered and is not
achievable here; handlers are required to be idempotent. The mechanisms are:

* ``dedupe_key`` — a unique constraint, so enqueuing the same logical work twice is a
  no-op rather than a duplicate.
* ``SELECT ... FOR UPDATE SKIP LOCKED`` — atomic claim with no cross-worker contention.
* lease + heartbeat — a worker that dies has its job reclaimed after the lease expires.
* ``fence_token`` — incremented on every claim. A resurrected worker holding a stale
  token is rejected by ``assert_fence`` before it can write.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from forge_core.backoff import backoff_seconds
from forge_core.db.models import Job
from forge_core.enums import JobKind, JobState
from forge_core.errors import FencedError
from forge_core.state_machine import JOB_MACHINE


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """A job this worker currently holds a lease on."""

    id: uuid.UUID
    kind: JobKind
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    fence_token: int
    dedupe_key: str
    lease_expires_at: datetime

    @property
    def is_final_attempt(self) -> bool:
        return self.attempts >= self.max_attempts


async def enqueue(
    session: AsyncSession,
    *,
    kind: JobKind,
    dedupe_key: str,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
    max_attempts: int = 3,
    available_at: datetime | None = None,
) -> uuid.UUID | None:
    """Insert a job, ignoring a conflicting ``dedupe_key``.

    Participates in the caller's transaction, so a pipeline row and its job are
    committed together or not at all.

    Returns the new job id, or None when an equivalent job already existed.
    """
    stmt = (
        pg_insert(Job)
        .values(
            id=uuid.uuid4(),
            kind=kind.value,
            state=JobState.PENDING.value,
            payload=payload or {},
            dedupe_key=dedupe_key,
            priority=priority,
            max_attempts=max_attempts,
            available_at=available_at or _now(),
        )
        .on_conflict_do_nothing(index_elements=[Job.dedupe_key])
        .returning(Job.id)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row


async def claim_one(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = 60,
    kinds: tuple[JobKind, ...] | None = None,
) -> ClaimedJob | None:
    """Atomically claim the highest-priority runnable job.

    ``FOR UPDATE SKIP LOCKED`` means concurrent workers never block each other and
    never claim the same row. The surrounding transaction is short: it covers the
    claim only, never the handler's work.
    """
    now = _now()
    # `kinds` is always bound as a parameter, never interpolated: when no filter is
    # requested it is the full set of kinds, which matches every row. This keeps the
    # statement a single constant string with no SQL built from variables.
    selected = kinds or tuple(JobKind)
    params: dict[str, Any] = {
        "now": now,
        "worker_id": worker_id,
        "lease": now + timedelta(seconds=lease_seconds),
        "kinds": [k.value for k in selected],
    }

    # The CTE selects and locks a single candidate row, then the UPDATE promotes it.
    sql = text(
        """
        WITH candidate AS (
            SELECT id
            FROM jobs
            WHERE state = 'PENDING'
              AND available_at <= :now
              AND cancel_requested = false
              AND kind = ANY(:kinds)
            ORDER BY priority ASC, available_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE jobs AS j
        SET state = 'RUNNING',
            worker_id = :worker_id,
            lease_expires_at = :lease,
            heartbeat_at = :now,
            started_at = COALESCE(j.started_at, :now),
            attempts = j.attempts + 1,
            fence_token = j.fence_token + 1,
            updated_at = :now
        FROM candidate
        WHERE j.id = candidate.id
        RETURNING j.id, j.kind, j.payload, j.attempts, j.max_attempts,
                  j.fence_token, j.dedupe_key, j.lease_expires_at
        """
    )
    result = await session.execute(sql, params)
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return ClaimedJob(
        id=row["id"],
        kind=JobKind(row["kind"]),
        payload=dict(row["payload"] or {}),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        fence_token=row["fence_token"],
        dedupe_key=row["dedupe_key"],
        lease_expires_at=row["lease_expires_at"],
    )


async def heartbeat(
    session: AsyncSession, job: ClaimedJob, *, worker_id: str, lease_seconds: int = 60
) -> bool:
    """Extend the lease. Returns False when the lease was lost (job reclaimed/fenced)."""
    now = _now()
    stmt = (
        update(Job)
        .where(
            Job.id == job.id,
            Job.worker_id == worker_id,
            Job.fence_token == job.fence_token,
            Job.state == JobState.RUNNING.value,
        )
        .values(
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0


async def assert_fence(session: AsyncSession, job: ClaimedJob) -> None:
    """Raise :class:`FencedError` if this worker no longer legitimately owns the job.

    Called before any externally visible side effect so a worker that was presumed
    dead, and whose job has been reclaimed, cannot act on stale authority.
    """
    current = await session.scalar(select(Job.fence_token).where(Job.id == job.id))
    if current is None or current != job.fence_token:
        raise FencedError(
            "Job lease was superseded by another worker; aborting to avoid a "
            "duplicate side effect.",
            details={"job_id": str(job.id), "held": job.fence_token, "current": current},
        )


async def is_cancel_requested(session: AsyncSession, job: ClaimedJob) -> bool:
    value = await session.scalar(select(Job.cancel_requested).where(Job.id == job.id))
    return bool(value)


async def complete(session: AsyncSession, job: ClaimedJob, *, worker_id: str) -> None:
    now = _now()
    JOB_MACHINE.validate(JobState.RUNNING, JobState.SUCCEEDED)
    await session.execute(
        update(Job)
        .where(Job.id == job.id, Job.fence_token == job.fence_token)
        .values(
            state=JobState.SUCCEEDED.value,
            finished_at=now,
            worker_id=worker_id,
            lease_expires_at=None,
            last_error="",
            updated_at=now,
        )
    )


async def fail(
    session: AsyncSession,
    job: ClaimedJob,
    *,
    error: str,
    retryable: bool = True,
    backoff_base: float = 2.0,
    backoff_cap: float = 300.0,
) -> JobState:
    """Record a failure, scheduling a retry or moving the job to the dead letter state.

    Returns the resulting state so the caller can log it accurately.
    """
    from forge_core.redaction import redact_text

    now = _now()
    exhausted = job.attempts >= job.max_attempts
    if not retryable or exhausted:
        target = JobState.DEAD_LETTER
        JOB_MACHINE.validate(JobState.RUNNING, target)
        await session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(
                state=target.value,
                finished_at=now,
                lease_expires_at=None,
                last_error=redact_text(error)[:4000],
                updated_at=now,
            )
        )
        return target

    target = JobState.PENDING
    JOB_MACHINE.validate(JobState.RUNNING, target)
    delay = backoff_seconds(job.attempts, base=backoff_base, cap=backoff_cap)
    await session.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(
            state=target.value,
            available_at=now + timedelta(seconds=delay),
            worker_id=None,
            lease_expires_at=None,
            last_error=redact_text(error)[:4000],
            updated_at=now,
        )
    )
    return target


async def request_cancel(session: AsyncSession, dedupe_key: str) -> bool:
    """Flag a job for cooperative cancellation. Idempotent: repeat calls are harmless."""
    now = _now()
    result = await session.execute(
        update(Job)
        .where(
            Job.dedupe_key == dedupe_key,
            Job.state.in_([JobState.PENDING.value, JobState.RUNNING.value]),
        )
        .values(cancel_requested=True, updated_at=now)
    )
    return (result.rowcount or 0) > 0


async def mark_canceled(session: AsyncSession, job: ClaimedJob) -> None:
    now = _now()
    await session.execute(
        update(Job)
        .where(Job.id == job.id, Job.fence_token == job.fence_token)
        .values(
            state=JobState.CANCELED.value,
            finished_at=now,
            lease_expires_at=None,
            updated_at=now,
        )
    )


async def reclaim_expired(session: AsyncSession, *, limit: int = 50) -> int:
    """Return jobs whose lease expired to the runnable pool.

    This is the crash-recovery path: a worker killed mid-job stops heartbeating, its
    lease lapses, and the job becomes claimable again. A job that has exhausted its
    attempts goes to the dead letter state instead of looping forever.
    """
    now = _now()
    sql = text(
        """
        WITH expired AS (
            SELECT id, attempts, max_attempts
            FROM jobs
            WHERE state = 'RUNNING'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < :now
            ORDER BY lease_expires_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT :limit
        )
        UPDATE jobs AS j
        SET state = CASE
                WHEN e.attempts >= e.max_attempts THEN 'DEAD_LETTER'
                ELSE 'PENDING'
            END,
            worker_id = NULL,
            lease_expires_at = NULL,
            available_at = CASE
                WHEN e.attempts >= e.max_attempts THEN j.available_at
                ELSE :now
            END,
            finished_at = CASE
                WHEN e.attempts >= e.max_attempts THEN :now
                ELSE NULL
            END,
            last_error = 'Worker lease expired; job reclaimed by the recovery sweeper.',
            updated_at = :now
        FROM expired AS e
        WHERE j.id = e.id
        RETURNING j.id
        """
    )
    result = await session.execute(sql, {"now": now, "limit": limit})
    return len(result.fetchall())


async def queue_depth(session: AsyncSession) -> dict[str, int]:
    """Counts per state, for the metrics endpoint and the dashboard."""
    rows = await session.execute(
        text("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
    )
    counts = {state.value: 0 for state in JobState}
    for row in rows.mappings():
        counts[row["state"]] = row["n"]
    return counts


__all__ = [
    "ClaimedJob",
    "assert_fence",
    "claim_one",
    "complete",
    "enqueue",
    "fail",
    "heartbeat",
    "is_cancel_requested",
    "mark_canceled",
    "queue_depth",
    "reclaim_expired",
    "request_cancel",
]

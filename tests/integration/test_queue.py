"""Durable queue semantics against a real PostgreSQL instance.

These cannot be meaningfully faked: SKIP LOCKED, row locking and transactional
visibility are the behaviour under test.
"""

from __future__ import annotations

import asyncio

import pytest
from forge_core.db import queue
from forge_core.enums import JobKind, JobState
from forge_core.errors import FencedError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

Maker = async_sessionmaker[AsyncSession]


async def _enqueue(maker: Maker, key: str, **kwargs: object) -> object:
    async with maker() as session, session.begin():
        return await queue.enqueue(
            session,
            kind=JobKind.RUN_PIPELINE,
            dedupe_key=key,
            **kwargs,  # type: ignore[arg-type]
        )


async def _claim(maker: Maker, worker: str, **kwargs: object) -> queue.ClaimedJob | None:
    async with maker() as session, session.begin():
        return await queue.claim_one(session, worker_id=worker, **kwargs)  # type: ignore[arg-type]


class TestEnqueue:
    async def test_enqueue_returns_an_id(self, sessionmaker_fixture: Maker) -> None:
        assert await _enqueue(sessionmaker_fixture, "job:1") is not None

    async def test_duplicate_dedupe_key_is_suppressed(self, sessionmaker_fixture: Maker) -> None:
        """Enqueuing the same logical work twice must not create two jobs."""
        first = await _enqueue(sessionmaker_fixture, "job:1")
        second = await _enqueue(sessionmaker_fixture, "job:1")
        assert first is not None
        assert second is None

    async def test_enqueue_participates_in_the_callers_transaction(
        self, sessionmaker_fixture: Maker
    ) -> None:
        """A rolled-back caller must not leave an orphaned job behind."""
        maker = sessionmaker_fixture
        try:
            async with maker() as session, session.begin():
                await queue.enqueue(session, kind=JobKind.RUN_PIPELINE, dedupe_key="rollback:1")
                raise RuntimeError("caller failed after enqueueing")
        except RuntimeError:
            pass
        async with maker() as session:
            remaining = await session.scalar(text("SELECT COUNT(*) FROM jobs"))
        assert remaining == 0


class TestClaiming:
    async def test_claim_marks_the_job_running(self, sessionmaker_fixture: Maker) -> None:
        await _enqueue(sessionmaker_fixture, "job:1")
        claimed = await _claim(sessionmaker_fixture, "w1")
        assert claimed is not None
        assert claimed.attempts == 1
        async with sessionmaker_fixture() as session:
            state = await session.scalar(text("SELECT state FROM jobs LIMIT 1"))
        assert state == JobState.RUNNING.value

    async def test_empty_queue_returns_none(self, sessionmaker_fixture: Maker) -> None:
        assert await _claim(sessionmaker_fixture, "w1") is None

    async def test_concurrent_workers_never_double_claim(self, sessionmaker_fixture: Maker) -> None:
        """The core guarantee: SKIP LOCKED gives each job to exactly one worker."""
        for index in range(8):
            await _enqueue(sessionmaker_fixture, f"job:{index}")

        results = await asyncio.gather(*(_claim(sessionmaker_fixture, f"w{n}") for n in range(16)))
        claimed = [job.dedupe_key for job in results if job is not None]
        assert len(claimed) == 8, "every job should be claimed exactly once"
        assert len(set(claimed)) == 8, "no job may be claimed twice"

    async def test_future_jobs_are_not_claimed(self, sessionmaker_fixture: Maker) -> None:
        await _enqueue(sessionmaker_fixture, "later:1")
        async with sessionmaker_fixture() as session, session.begin():
            await session.execute(text("UPDATE jobs SET available_at = now() + interval '1 hour'"))
        assert await _claim(sessionmaker_fixture, "w1") is None

    async def test_priority_orders_claims(self, sessionmaker_fixture: Maker) -> None:
        await _enqueue(sessionmaker_fixture, "low", priority=200)
        await _enqueue(sessionmaker_fixture, "high", priority=1)
        claimed = await _claim(sessionmaker_fixture, "w1")
        assert claimed is not None
        assert claimed.dedupe_key == "high"

    async def test_kind_filter_is_respected(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        async with maker() as session, session.begin():
            await queue.enqueue(session, kind=JobKind.RUN_PIPELINE, dedupe_key="p")
            await queue.enqueue(session, kind=JobKind.RUN_DEPLOYMENT, dedupe_key="d")
        claimed = await _claim(maker, "w1", kinds=(JobKind.RUN_DEPLOYMENT,))
        assert claimed is not None
        assert claimed.kind is JobKind.RUN_DEPLOYMENT

    async def test_cancelled_jobs_are_not_claimed(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        await _enqueue(maker, "cancel:1")
        async with maker() as session, session.begin():
            await queue.request_cancel(session, "cancel:1")
        assert await _claim(maker, "w1") is None


class TestLeasesAndFencing:
    async def test_expired_lease_is_reclaimed(self, sessionmaker_fixture: Maker) -> None:
        """This is crash recovery: a dead worker's job returns to the queue."""
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1", max_attempts=5)
        first = await _claim(maker, "dead-worker")
        assert first is not None

        async with maker() as session, session.begin():
            await session.execute(
                text("UPDATE jobs SET lease_expires_at = now() - interval '1 minute'")
            )
        async with maker() as session, session.begin():
            reclaimed = await queue.reclaim_expired(session)
        assert reclaimed == 1

        second = await _claim(maker, "live-worker")
        assert second is not None
        assert second.fence_token > first.fence_token

    async def test_stale_worker_is_fenced_out(self, sessionmaker_fixture: Maker) -> None:
        """A resurrected worker must not act on authority it no longer holds."""
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1", max_attempts=5)
        stale = await _claim(maker, "w-old")
        assert stale is not None
        async with maker() as session, session.begin():
            await session.execute(
                text("UPDATE jobs SET lease_expires_at = now() - interval '1 minute'")
            )
            await queue.reclaim_expired(session)
        await _claim(maker, "w-new")

        async with maker() as session:
            with pytest.raises(FencedError):
                await queue.assert_fence(session, stale)

    async def test_stale_heartbeat_is_rejected(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1", max_attempts=5)
        stale = await _claim(maker, "w-old")
        assert stale is not None
        async with maker() as session, session.begin():
            await session.execute(
                text("UPDATE jobs SET lease_expires_at = now() - interval '1 minute'")
            )
            await queue.reclaim_expired(session)
        await _claim(maker, "w-new")

        async with maker() as session, session.begin():
            assert not await queue.heartbeat(session, stale, worker_id="w-old")

    async def test_heartbeat_extends_the_lease(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1")
        claimed = await _claim(maker, "w1", lease_seconds=5)
        assert claimed is not None
        async with maker() as session, session.begin():
            assert await queue.heartbeat(session, claimed, worker_id="w1", lease_seconds=600)
        async with maker() as session, session.begin():
            assert await queue.reclaim_expired(session) == 0

    async def test_exhausted_job_dead_letters_instead_of_looping(
        self, sessionmaker_fixture: Maker
    ) -> None:
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1", max_attempts=1)
        await _claim(maker, "w1")
        async with maker() as session, session.begin():
            await session.execute(
                text("UPDATE jobs SET lease_expires_at = now() - interval '1 minute'")
            )
            await queue.reclaim_expired(session)
        async with maker() as session:
            state = await session.scalar(text("SELECT state FROM jobs LIMIT 1"))
        assert state == JobState.DEAD_LETTER.value


class TestOutcomes:
    async def test_retry_then_dead_letter(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1", max_attempts=2)
        states = []
        for _ in range(3):
            claimed = await _claim(maker, "w1")
            if claimed is None:
                break
            async with maker() as session, session.begin():
                states.append(await queue.fail(session, claimed, error="boom"))
            async with maker() as session, session.begin():
                await session.execute(
                    text("UPDATE jobs SET available_at = now() - interval '1 second'")
                )
        assert states == [JobState.PENDING, JobState.DEAD_LETTER]

    async def test_non_retryable_failure_dead_letters_immediately(
        self, sessionmaker_fixture: Maker
    ) -> None:
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1", max_attempts=10)
        claimed = await _claim(maker, "w1")
        assert claimed is not None
        async with maker() as session, session.begin():
            result = await queue.fail(session, claimed, error="unrecoverable", retryable=False)
        assert result is JobState.DEAD_LETTER

    async def test_failure_message_is_redacted(self, sessionmaker_fixture: Maker) -> None:
        """Job errors are shown in the dashboard, so they must not leak secrets."""
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1")
        claimed = await _claim(maker, "w1")
        assert claimed is not None
        async with maker() as session, session.begin():
            await queue.fail(session, claimed, error="failed api_key=supersecret123")
        async with maker() as session:
            stored = await session.scalar(text("SELECT last_error FROM jobs LIMIT 1"))
        assert "supersecret123" not in str(stored)
        assert "REDACTED" in str(stored)

    async def test_complete_marks_succeeded(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1")
        claimed = await _claim(maker, "w1")
        assert claimed is not None
        async with maker() as session, session.begin():
            await queue.complete(session, claimed, worker_id="w1")
        async with maker() as session:
            state = await session.scalar(text("SELECT state FROM jobs LIMIT 1"))
        assert state == JobState.SUCCEEDED.value

    async def test_queue_depth_counts_every_state(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        await _enqueue(maker, "a")
        await _enqueue(maker, "b")
        await _claim(maker, "w1")
        async with maker() as session:
            depth = await queue.queue_depth(session)
        assert depth[JobState.PENDING.value] == 1
        assert depth[JobState.RUNNING.value] == 1
        assert set(depth) == {s.value for s in JobState}

    async def test_repeated_cancel_requests_are_idempotent(
        self, sessionmaker_fixture: Maker
    ) -> None:
        maker = sessionmaker_fixture
        await _enqueue(maker, "job:1")
        async with maker() as session, session.begin():
            assert await queue.request_cancel(session, "job:1")
        async with maker() as session, session.begin():
            # Second call changes nothing and must not raise.
            await queue.request_cancel(session, "job:1")
        async with maker() as session:
            count = await session.scalar(text("SELECT COUNT(*) FROM jobs WHERE cancel_requested"))
        assert count == 1

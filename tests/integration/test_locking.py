"""Deployment locking: the guard against concurrent rollouts of one service."""

from __future__ import annotations

import asyncio

import pytest
from forge_core.errors import FencedError, LockAcquisitionError
from forge_worker import locking
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

Maker = async_sessionmaker[AsyncSession]
RESOURCE = "deploy:svc:dev"


async def _acquire(maker: Maker, owner: str, ttl: int = 300) -> locking.DeploymentLock:
    async with maker() as session, session.begin():
        return await locking.acquire(session, RESOURCE, owner, ttl_seconds=ttl)


async def _expire(maker: Maker) -> None:
    async with maker() as session, session.begin():
        await session.execute(
            text("UPDATE deployment_locks SET expires_at = now() - interval '1 second'")
        )


class TestMutualExclusion:
    async def test_second_holder_is_refused(self, sessionmaker_fixture: Maker) -> None:
        await _acquire(sessionmaker_fixture, "worker-a")
        with pytest.raises(LockAcquisitionError, match="already in progress"):
            await _acquire(sessionmaker_fixture, "worker-b")

    async def test_refusal_names_the_current_holder(self, sessionmaker_fixture: Maker) -> None:
        await _acquire(sessionmaker_fixture, "worker-a")
        with pytest.raises(LockAcquisitionError) as caught:
            await _acquire(sessionmaker_fixture, "worker-b")
        assert caught.value.details["held_by"] == "worker-a"

    async def test_exactly_one_winner_under_contention(self, sessionmaker_fixture: Maker) -> None:
        async def contend(index: int) -> bool:
            try:
                await _acquire(sessionmaker_fixture, f"worker-{index}", ttl=60)
                return True
            except LockAcquisitionError:
                return False
            except Exception:
                # A serialisation failure is also a legitimate loss.
                return False

        results = await asyncio.gather(*(contend(n) for n in range(16)))
        assert sum(results) == 1

    async def test_same_owner_is_reentrant(self, sessionmaker_fixture: Maker) -> None:
        """A worker retrying its own deployment must not deadlock itself."""
        first = await _acquire(sessionmaker_fixture, "worker-a")
        second = await _acquire(sessionmaker_fixture, "worker-a")
        assert second.token > first.token

    async def test_different_resources_do_not_contend(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        async with maker() as session, session.begin():
            await locking.acquire(session, "deploy:a:dev", "w1")
        async with maker() as session, session.begin():
            await locking.acquire(session, "deploy:b:dev", "w2")


class TestExpiryAndFencing:
    async def test_expired_lock_can_be_taken_over(self, sessionmaker_fixture: Maker) -> None:
        await _acquire(sessionmaker_fixture, "dead-worker")
        await _expire(sessionmaker_fixture)
        taken = await _acquire(sessionmaker_fixture, "live-worker")
        assert taken.owner == "live-worker"

    async def test_previous_holder_is_fenced_after_takeover(
        self, sessionmaker_fixture: Maker
    ) -> None:
        """The key property: a stalled worker cannot resume and clobber the new one."""
        stale = await _acquire(sessionmaker_fixture, "dead-worker")
        await _expire(sessionmaker_fixture)
        await _acquire(sessionmaker_fixture, "live-worker")

        async with sessionmaker_fixture() as session:
            with pytest.raises(FencedError):
                await locking.assert_held(session, stale)

    async def test_stale_renew_is_rejected(self, sessionmaker_fixture: Maker) -> None:
        stale = await _acquire(sessionmaker_fixture, "dead-worker")
        await _expire(sessionmaker_fixture)
        await _acquire(sessionmaker_fixture, "live-worker")
        async with sessionmaker_fixture() as session, session.begin():
            with pytest.raises(FencedError):
                await locking.renew(session, stale)

    async def test_token_increases_monotonically(self, sessionmaker_fixture: Maker) -> None:
        tokens = []
        for index in range(4):
            await _expire(sessionmaker_fixture)
            tokens.append((await _acquire(sessionmaker_fixture, f"w{index}")).token)
        assert tokens == sorted(tokens)
        assert len(set(tokens)) == 4

    async def test_renew_extends_a_held_lock(self, sessionmaker_fixture: Maker) -> None:
        lock = await _acquire(sessionmaker_fixture, "worker-a", ttl=1)
        async with sessionmaker_fixture() as session, session.begin():
            renewed = await locking.renew(session, lock, ttl_seconds=600)
        assert renewed.expires_at > lock.expires_at

    async def test_assert_held_passes_for_the_real_owner(self, sessionmaker_fixture: Maker) -> None:
        lock = await _acquire(sessionmaker_fixture, "worker-a")
        async with sessionmaker_fixture() as session:
            await locking.assert_held(session, lock)


class TestRelease:
    async def test_owner_can_release(self, sessionmaker_fixture: Maker) -> None:
        lock = await _acquire(sessionmaker_fixture, "worker-a")
        async with sessionmaker_fixture() as session, session.begin():
            assert await locking.release(session, lock)

    async def test_non_owner_cannot_release(self, sessionmaker_fixture: Maker) -> None:
        """Releasing someone else's lock would reintroduce the race it prevents."""
        stale = await _acquire(sessionmaker_fixture, "worker-a")
        await _expire(sessionmaker_fixture)
        await _acquire(sessionmaker_fixture, "worker-b")
        async with sessionmaker_fixture() as session, session.begin():
            assert not await locking.release(session, stale)

    async def test_release_then_reacquire(self, sessionmaker_fixture: Maker) -> None:
        lock = await _acquire(sessionmaker_fixture, "worker-a")
        async with sessionmaker_fixture() as session, session.begin():
            await locking.release(session, lock)
        await _acquire(sessionmaker_fixture, "worker-b")

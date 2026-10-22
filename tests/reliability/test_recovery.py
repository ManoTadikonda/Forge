"""Failure and recovery scenarios.

Each test exercises a way the platform can be interrupted — a worker dying mid-job,
a duplicate delivery, two deployments racing, a restart — and asserts the observable
outcome rather than that some method was called.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest
from forge_core.db import queue
from forge_core.db.models import (
    Artifact,
    Deployment,
    Environment,
    PipelineRun,
    Repository,
    Service,
)
from forge_core.enums import DeploymentState, JobKind, JobState, PipelineState
from forge_core.errors import FencedError, LockAcquisitionError
from forge_worker import locking
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.reliability, pytest.mark.integration, pytest.mark.asyncio]

Maker = async_sessionmaker[AsyncSession]


async def _seed_service(maker: Maker) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create the minimum rows needed to describe a deployable service."""
    async with maker() as session, session.begin():
        environment = Environment(id=uuid.uuid4(), name="development", config={})
        repository = Repository(
            id=uuid.uuid4(),
            name="demo",
            url="https://github.com/demo/app",
            slug="github.com/demo/app",
        )
        session.add_all([environment, repository])
        await session.flush()

        service = Service(id=uuid.uuid4(), name="demo", environment_id=environment.id, config={})
        artifact = Artifact(
            id=uuid.uuid4(),
            digest=f"sha256:{uuid.uuid4().hex * 2}",
            image_name="demo",
            version="1.0.0",
            registry_name="local",
            provenance={},
            scan_summary={},
        )
        session.add_all([service, artifact])
        await session.flush()
        return service.id, environment.id, artifact.id


class TestWorkerCrashDuringJob:
    async def test_job_is_reclaimed_after_a_worker_dies(self, sessionmaker_fixture: Maker) -> None:
        """A worker killed mid-job must not strand the work forever."""
        maker = sessionmaker_fixture
        async with maker() as session, session.begin():
            await queue.enqueue(
                session,
                kind=JobKind.RUN_PIPELINE,
                dedupe_key="crash:1",
                payload={"pipeline_run_id": str(uuid.uuid4())},
                max_attempts=3,
            )
        async with maker() as session, session.begin():
            crashed = await queue.claim_one(session, worker_id="doomed", lease_seconds=1)
        assert crashed is not None

        # The worker dies: no heartbeat, so the lease simply lapses.
        async with maker() as session, session.begin():
            await session.execute(
                text("UPDATE jobs SET lease_expires_at = now() - interval '5 seconds'")
            )
        async with maker() as session, session.begin():
            assert await queue.reclaim_expired(session) == 1

        async with maker() as session, session.begin():
            recovered = await queue.claim_one(session, worker_id="survivor")
        assert recovered is not None
        assert recovered.dedupe_key == "crash:1"
        assert recovered.attempts == 2, "the retry must be counted"

    async def test_resurrected_worker_cannot_complete_a_reassigned_job(
        self, sessionmaker_fixture: Maker
    ) -> None:
        """The fencing property: a zombie must not finish work someone else owns."""
        maker = sessionmaker_fixture
        async with maker() as session, session.begin():
            await queue.enqueue(
                session,
                kind=JobKind.RUN_PIPELINE,
                dedupe_key="zombie:1",
                max_attempts=5,
            )
        async with maker() as session, session.begin():
            zombie = await queue.claim_one(session, worker_id="zombie")
        assert zombie is not None

        async with maker() as session, session.begin():
            await session.execute(
                text("UPDATE jobs SET lease_expires_at = now() - interval '5 seconds'")
            )
            await queue.reclaim_expired(session)
        async with maker() as session, session.begin():
            await queue.claim_one(session, worker_id="replacement")

        async with maker() as session:
            with pytest.raises(FencedError):
                await queue.assert_fence(session, zombie)

    async def test_recovery_sweeper_handles_many_abandoned_jobs(
        self, sessionmaker_fixture: Maker
    ) -> None:
        maker = sessionmaker_fixture
        for index in range(12):
            async with maker() as session, session.begin():
                await queue.enqueue(
                    session,
                    kind=JobKind.RUN_PIPELINE,
                    dedupe_key=f"bulk:{index}",
                    max_attempts=3,
                )
        for index in range(12):
            async with maker() as session, session.begin():
                await queue.claim_one(session, worker_id=f"dead-{index}")
        async with maker() as session, session.begin():
            await session.execute(
                text("UPDATE jobs SET lease_expires_at = now() - interval '1 minute'")
            )
        async with maker() as session, session.begin():
            assert await queue.reclaim_expired(session, limit=50) == 12


class TestDuplicateDelivery:
    async def test_duplicate_enqueue_creates_one_job(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        for _ in range(5):
            async with maker() as session, session.begin():
                await queue.enqueue(session, kind=JobKind.RUN_PIPELINE, dedupe_key="same-work")
        async with maker() as session:
            count = await session.scalar(text("SELECT COUNT(*) FROM jobs"))
        assert count == 1

    async def test_concurrent_duplicate_enqueue_is_safe(self, sessionmaker_fixture: Maker) -> None:
        """The unique constraint, not application-level checking, is what guarantees it."""
        maker = sessionmaker_fixture

        async def attempt() -> None:
            # A losing racer raises on the unique constraint; that is the point.
            with contextlib.suppress(Exception):
                async with maker() as session, session.begin():
                    await queue.enqueue(session, kind=JobKind.RUN_PIPELINE, dedupe_key="race")

        await asyncio.gather(*(attempt() for _ in range(10)))
        async with maker() as session:
            count = await session.scalar(text("SELECT COUNT(*) FROM jobs"))
        assert count == 1

    async def test_handler_redelivery_is_idempotent_for_finished_work(
        self, sessionmaker_fixture: Maker
    ) -> None:
        """At-least-once delivery means a finished run may be handed over again."""
        from forge_core.config import Settings
        from forge_worker.handlers.pipeline import PipelineRunner

        maker = sessionmaker_fixture
        async with maker() as session, session.begin():
            repository = Repository(
                id=uuid.uuid4(),
                name="demo",
                url="https://github.com/demo/app",
                slug="github.com/demo/app",
            )
            session.add(repository)
            await session.flush()
            run = PipelineRun(
                id=uuid.uuid4(),
                repository_id=repository.id,
                repository_label=repository.slug,
                run_number=1,
                state=PipelineState.SUCCEEDED.value,
            )
            session.add(run)
            await session.flush()
            run_id = run.id

        runner = PipelineRunner(
            sessionmaker=maker,
            builder=None,
            registry=None,
            settings=Settings(environment="test"),
            worker_id="w1",
        )
        # Must return the terminal state without re-running anything.
        assert await runner.run(run_id) is PipelineState.SUCCEEDED


class TestConcurrentDeployments:
    async def test_second_deployment_to_the_same_service_is_blocked(
        self, sessionmaker_fixture: Maker
    ) -> None:
        maker = sessionmaker_fixture
        service_id, environment_id, _ = await _seed_service(maker)
        resource = locking.resource_key(service_id, environment_id)

        async with maker() as session, session.begin():
            await locking.acquire(session, resource, "deployment-a")
        with pytest.raises(LockAcquisitionError):
            async with maker() as session, session.begin():
                await locking.acquire(session, resource, "deployment-b")

    async def test_racing_deployments_yield_exactly_one_winner(
        self, sessionmaker_fixture: Maker
    ) -> None:
        maker = sessionmaker_fixture
        service_id, environment_id, _ = await _seed_service(maker)
        resource = locking.resource_key(service_id, environment_id)

        async def attempt(index: int) -> bool:
            try:
                async with maker() as session, session.begin():
                    await locking.acquire(session, resource, f"deployment-{index}")
                return True
            except Exception:
                return False

        assert sum(await asyncio.gather(*(attempt(n) for n in range(12)))) == 1

    async def test_different_environments_deploy_independently(
        self, sessionmaker_fixture: Maker
    ) -> None:
        """A staging rollout must not block a development one."""
        maker = sessionmaker_fixture
        service_id, environment_id, _ = await _seed_service(maker)
        other_environment = uuid.uuid4()

        async with maker() as session, session.begin():
            await locking.acquire(session, locking.resource_key(service_id, environment_id), "a")
        async with maker() as session, session.begin():
            await locking.acquire(session, locking.resource_key(service_id, other_environment), "b")


class TestDurabilityAcrossRestart:
    async def test_history_survives_a_new_connection_pool(
        self, sessionmaker_fixture: Maker, schema: str
    ) -> None:
        """State lives in PostgreSQL, so a process restart must not lose it."""
        from forge_core.db.session import create_engine

        maker = sessionmaker_fixture
        service_id, environment_id, artifact_id = await _seed_service(maker)
        async with maker() as session, session.begin():
            deployment = Deployment(
                id=uuid.uuid4(),
                service_id=service_id,
                environment_id=environment_id,
                artifact_id=artifact_id,
                state=DeploymentState.ROLLED_BACK.value,
                deployment_number=1,
                canary_steps={"steps": [10, 100]},
                rollback_reason="error rate breached",
                rollback_verified=True,
                health_summary={},
                current_weight=0,
            )
            session.add(deployment)
            await session.flush()
            deployment_id = deployment.id

        # Simulate a restart: dispose everything and connect anew.
        fresh_engine = create_engine(schema, pool_size=2, max_overflow=0)
        try:
            fresh_maker = async_sessionmaker(fresh_engine, expire_on_commit=False)
            async with fresh_maker() as session:
                restored = await session.get(Deployment, deployment_id)
                assert restored is not None
                assert restored.state == DeploymentState.ROLLED_BACK.value
                assert restored.rollback_verified is True
                assert restored.rollback_reason == "error rate breached"
        finally:
            await fresh_engine.dispose()

    async def test_pending_jobs_are_still_claimable_after_reconnect(
        self, sessionmaker_fixture: Maker, schema: str
    ) -> None:
        from forge_core.db.session import create_engine

        maker = sessionmaker_fixture
        async with maker() as session, session.begin():
            await queue.enqueue(session, kind=JobKind.RUN_DEPLOYMENT, dedupe_key="survives-restart")

        fresh_engine = create_engine(schema, pool_size=2, max_overflow=0)
        try:
            fresh_maker = async_sessionmaker(fresh_engine, expire_on_commit=False)
            async with fresh_maker() as session, session.begin():
                claimed = await queue.claim_one(session, worker_id="restarted-worker")
            assert claimed is not None
            assert claimed.dedupe_key == "survives-restart"
        finally:
            await fresh_engine.dispose()


class TestCancellation:
    async def test_repeated_cancellation_is_idempotent(self, sessionmaker_fixture: Maker) -> None:
        maker = sessionmaker_fixture
        async with maker() as session, session.begin():
            await queue.enqueue(session, kind=JobKind.RUN_PIPELINE, dedupe_key="cancel-me")
        for _ in range(5):
            async with maker() as session, session.begin():
                await queue.request_cancel(session, "cancel-me")
        async with maker() as session:
            flagged = await session.scalar(text("SELECT COUNT(*) FROM jobs WHERE cancel_requested"))
        assert flagged == 1

    async def test_cancelling_a_finished_job_changes_nothing(
        self, sessionmaker_fixture: Maker
    ) -> None:
        maker = sessionmaker_fixture
        async with maker() as session, session.begin():
            await queue.enqueue(session, kind=JobKind.RUN_PIPELINE, dedupe_key="done")
        async with maker() as session, session.begin():
            claimed = await queue.claim_one(session, worker_id="w1")
            assert claimed is not None
            await queue.complete(session, claimed, worker_id="w1")
        async with maker() as session, session.begin():
            assert not await queue.request_cancel(session, "done")
        async with maker() as session:
            state = await session.scalar(text("SELECT state FROM jobs LIMIT 1"))
        assert state == JobState.SUCCEEDED.value

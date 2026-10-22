"""Worker runtime.

One process runs N concurrent workers plus a recovery sweeper. Each worker:

1. claims a job atomically (``FOR UPDATE SKIP LOCKED``)
2. starts a heartbeat task that renews the lease while the handler runs
3. runs the handler **outside** any database transaction
4. records success, retry, or dead-letter

If the process dies, leases lapse and the sweeper returns the work to the queue. The
handler may therefore run more than once for the same logical job, which is why
handlers are idempotent and check their own terminal state before acting.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import uuid
from pathlib import Path
from typing import Any

from forge_core.config import Settings
from forge_core.db import queue
from forge_core.db.queue import ClaimedJob
from forge_core.enums import JobKind
from forge_core.errors import FencedError, LockAcquisitionError
from forge_core.logging import bind_context, get_logger, reset_context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge_worker.builders.buildkit import BuildKitBuilder
from forge_worker.builders.local import LocalProcessBuilder
from forge_worker.controller import DeploymentController
from forge_worker.deploy.docker_adapter import DockerDeploymentAdapter
from forge_worker.deploy.process_adapter import ProcessDeploymentAdapter
from forge_worker.handlers.pipeline import PipelineRunner
from forge_worker.registry.ecr import EcrRegistry
from forge_worker.registry.local import LocalContentAddressedRegistry

logger = get_logger("forge.worker")


def build_builder(settings: Settings) -> Any:
    return (
        BuildKitBuilder(settings)
        if settings.builder == "buildkit"
        else LocalProcessBuilder(settings)
    )


def build_registry(settings: Settings) -> Any:
    return (
        EcrRegistry()
        if settings.registry == "ecr"
        else LocalContentAddressedRegistry(Path(settings.artifact_root))
    )


def build_adapter(settings: Settings) -> Any:
    if settings.deploy_adapter == "docker":
        return DockerDeploymentAdapter(router_admin_url=settings.router_admin_url)
    return ProcessDeploymentAdapter(
        router_admin_url=settings.router_admin_url,
        run_root=Path(settings.workspace_root).parent / "releases",
        host=settings.router_public_host,
    )


class Worker:
    """A single concurrent slot that claims and executes jobs."""

    def __init__(
        self,
        *,
        name: str,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        builder: Any,
        registry: Any,
        adapter: Any,
        stop_event: asyncio.Event,
    ) -> None:
        self.name = name
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._builder = builder
        self._registry = registry
        self._adapter = adapter
        self._stop = stop_event

    async def run(self) -> None:
        logger.info("worker_started", worker=self.name)
        while not self._stop.is_set():
            try:
                claimed = await self._claim()
            except Exception:
                logger.exception("claim_failed", worker=self.name)
                await self._wait(self._settings.worker_poll_interval_seconds)
                continue

            if claimed is None:
                await self._wait(self._settings.worker_poll_interval_seconds)
                continue

            await self._execute(claimed)
        logger.info("worker_stopped", worker=self.name)

    async def _wait(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def _claim(self) -> ClaimedJob | None:
        # Short transaction: the claim only, never the handler's work.
        async with self._sessionmaker() as session, session.begin():
            return await queue.claim_one(
                session,
                worker_id=self.name,
                lease_seconds=self._settings.job_lease_seconds,
            )

    async def _heartbeat(self, job: ClaimedJob) -> None:
        """Renew the lease until cancelled. Stops the job if the lease is lost."""
        interval = self._settings.job_heartbeat_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                async with self._sessionmaker() as session, session.begin():
                    alive = await queue.heartbeat(
                        session,
                        job,
                        worker_id=self.name,
                        lease_seconds=self._settings.job_lease_seconds,
                    )
                if not alive:
                    logger.warning(
                        "lease_lost",
                        worker=self.name,
                        job_id=str(job.id),
                    )
                    return
            except Exception:
                logger.exception("heartbeat_failed", job_id=str(job.id))

    async def _execute(self, job: ClaimedJob) -> None:
        tokens = bind_context(job_id=str(job.id), actor=self.name)
        heartbeat = asyncio.create_task(self._heartbeat(job))
        try:
            async with self._sessionmaker() as session:
                if await queue.is_cancel_requested(session, job):
                    async with session.begin():
                        await queue.mark_canceled(session, job)
                    logger.info("job_cancelled", job_id=str(job.id))
                    return

            logger.info(
                "job_started",
                job_id=str(job.id),
                kind=str(job.kind),
                attempt=job.attempts,
                max_attempts=job.max_attempts,
            )
            await self._dispatch(job)

            async with self._sessionmaker() as session, session.begin():
                await queue.complete(session, job, worker_id=self.name)
            logger.info("job_succeeded", job_id=str(job.id))

        except FencedError as fenced:
            # Another worker owns this job now. Do not retry and do not fail it:
            # the current owner decides its outcome.
            logger.warning("job_fenced", job_id=str(job.id), detail=fenced.message)
        except LockAcquisitionError as busy:
            # The target resource is busy. Retry later rather than failing.
            async with self._sessionmaker() as session, session.begin():
                await queue.fail(
                    session,
                    job,
                    error=busy.message,
                    retryable=True,
                    backoff_base=self._settings.job_backoff_base_seconds,
                    backoff_cap=self._settings.job_backoff_max_seconds,
                )
            logger.info("job_deferred", job_id=str(job.id), reason=busy.message)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("job_failed", job_id=str(job.id))
            async with self._sessionmaker() as session, session.begin():
                state = await queue.fail(
                    session,
                    job,
                    error=error,
                    retryable=True,
                    backoff_base=self._settings.job_backoff_base_seconds,
                    backoff_cap=self._settings.job_backoff_max_seconds,
                )
            logger.info(
                "job_outcome_recorded",
                job_id=str(job.id),
                state=str(state),
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            reset_context(tokens)

    async def _dispatch(self, job: ClaimedJob) -> None:
        if job.kind is JobKind.RUN_PIPELINE:
            run_id = uuid.UUID(str(job.payload["pipeline_run_id"]))
            tokens = bind_context(pipeline_id=str(run_id))
            try:
                runner = PipelineRunner(
                    sessionmaker=self._sessionmaker,
                    builder=self._builder,
                    registry=self._registry,
                    settings=self._settings,
                    worker_id=self.name,
                )
                await runner.run(run_id)
            finally:
                reset_context(tokens)
            return

        if job.kind is JobKind.RUN_DEPLOYMENT:
            deployment_id = uuid.UUID(str(job.payload["deployment_id"]))
            tokens = bind_context(deployment_id=str(deployment_id))
            try:
                controller = DeploymentController(
                    sessionmaker=self._sessionmaker,
                    adapter=self._adapter,
                    settings=self._settings,
                    worker_id=self.name,
                )
                await controller.run(deployment_id)
            finally:
                reset_context(tokens)
            return

        raise ValueError(f"No handler registered for job kind {job.kind}.")


class RecoverySweeper:
    """Returns jobs whose worker died to the runnable pool."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        stop_event: asyncio.Event,
        interval_seconds: float = 10.0,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._stop = stop_event
        self._interval = interval_seconds

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                async with self._sessionmaker() as session, session.begin():
                    reclaimed = await queue.reclaim_expired(session)
                if reclaimed:
                    logger.warning("jobs_reclaimed", count=reclaimed)
            except Exception:
                logger.exception("sweeper_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)


class WorkerPool:
    """Owns the worker tasks, the sweeper, and graceful shutdown."""

    def __init__(
        self, *, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._stop = asyncio.Event()
        host = socket.gethostname()
        self._prefix = f"{host}:{os.getpid()}"
        self._adapter = build_adapter(settings)

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        builder = build_builder(self._settings)
        registry = build_registry(self._settings)

        for component, label in (
            (builder, "builder"),
            (registry, "registry"),
            (self._adapter, "adapter"),
        ):
            usable, reason = await component.available()
            logger.info(
                "component_probe",
                component=label,
                name=component.name,
                usable=usable,
                detail=reason,
            )
            if not usable and label == "builder":
                logger.error("builder_unusable", detail=reason)

        workers = [
            Worker(
                name=f"{self._prefix}:w{index}",
                sessionmaker=self._sessionmaker,
                settings=self._settings,
                builder=builder,
                registry=registry,
                adapter=self._adapter,
                stop_event=self._stop,
            )
            for index in range(self._settings.worker_concurrency)
        ]
        sweeper = RecoverySweeper(
            sessionmaker=self._sessionmaker,
            settings=self._settings,
            stop_event=self._stop,
        )
        logger.info(
            "worker_pool_started",
            concurrency=self._settings.worker_concurrency,
            prefix=self._prefix,
        )
        await asyncio.gather(*(w.run() for w in workers), sweeper.run())

    async def aclose(self) -> None:
        close = getattr(self._adapter, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                await close()


async def main() -> None:  # pragma: no cover - process entry point
    from forge_core.config import get_settings
    from forge_core.db.session import embedded_database_url, get_sessionmaker, init_engine
    from forge_core.logging import configure_logging

    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format, service="forge-worker")
    url = settings.database_url or embedded_database_url()
    init_engine(
        url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        statement_timeout_ms=settings.database_statement_timeout_ms,
    )

    pool = WorkerPool(sessionmaker=get_sessionmaker(), settings=settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, pool.request_stop)
    try:
        await pool.run()
    finally:
        await pool.aclose()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())

"""Shared test fixtures.

Integration tests run against a **real** PostgreSQL 16 instance (embedded via
``pgserver`` when ``FORGE_TEST_DATABASE_URL`` is not set) and a real Redis when one
is reachable. Nothing important is mocked: the queue's concurrency semantics, the
locking behaviour and the migration all depend on genuine PostgreSQL, so testing
them against a fake would test nothing.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]

# Must be set before forge_core.config is first imported.
os.environ.setdefault("FORGE_ENVIRONMENT", "test")
os.environ.setdefault("FORGE_SECRET_KEY", "test-secret-key-at-least-32-characters-long!!")
os.environ.setdefault("FORGE_ALLOW_UNSANDBOXED_BUILDS", "true")
os.environ.setdefault("FORGE_RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("FORGE_PG_DATA_DIR", str(ROOT / ".forge" / "pgdata-test"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: requires a live PostgreSQL")
    config.addinivalue_line("markers", "reliability: crash and recovery scenarios")
    config.addinivalue_line("markers", "slow: long-running orchestration tests")


# ---------------------------------------------------------------------- database


@pytest.fixture(scope="session")
def database_url() -> str:
    """A PostgreSQL URL for the test session, or skip the whole integration suite."""
    configured = os.environ.get("FORGE_TEST_DATABASE_URL", "")
    if configured:
        return configured
    try:
        from forge_core.db.session import embedded_database_url

        return embedded_database_url(Path(os.environ["FORGE_PG_DATA_DIR"]))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(
            f"No PostgreSQL available for integration tests ({exc}). Set "
            f"FORGE_TEST_DATABASE_URL to run them."
        )


@pytest.fixture(scope="session")
def schema(database_url: str) -> str:
    """Create the schema once per session.

    Deliberately synchronous: an async session-scoped fixture would need a
    session-scoped event loop, which then conflicts with the per-test loops that
    asyncpg connections are bound to. Creating the schema in its own short-lived
    loop sidesteps that entirely.
    """
    import asyncio

    import forge_core.db.models  # noqa: F401  registers every table
    from forge_core.db.base import Base
    from forge_core.db.session import create_engine

    async def _create() -> None:
        engine = create_engine(database_url, pool_size=1, max_overflow=0)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_create())
    return database_url


@pytest_asyncio.fixture
async def engine(schema: str) -> AsyncIterator[object]:
    """A per-test engine, bound to the test's own event loop."""
    from forge_core.db.session import create_engine

    instance = create_engine(schema, pool_size=5, max_overflow=5)
    try:
        yield instance
    finally:
        await instance.dispose()


@pytest_asyncio.fixture
async def sessionmaker_fixture(
    engine: object,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A sessionmaker over a database truncated before each test."""
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)  # type: ignore[arg-type]
    async with maker() as session, session.begin():
        # TRUNCATE ... CASCADE is far cheaper than recreating the schema per test,
        # and RESTART IDENTITY keeps sequence-derived values predictable.
        await session.execute(
            text(
                "TRUNCATE TABLE "
                "health_samples, deployment_events, incidents, releases, deployments, "
                "log_chunks, pipeline_stages, artifacts, pipeline_runs, jobs, "
                "deployment_locks, webhook_deliveries, audit_events, sessions, "
                "services, environments, repositories, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield maker


@pytest_asyncio.fixture
async def session(
    sessionmaker_fixture: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker_fixture() as instance:
        yield instance


# ------------------------------------------------------------------------ helpers


@pytest.fixture
def settings() -> object:
    from forge_core.config import Settings

    return Settings(
        environment="test",
        allow_unsandboxed_builds=True,
        canary_steps=(50, 100),
        canary_step_seconds=1,
        health_min_samples=5,
        job_lease_seconds=5,
    )


@pytest.fixture
def frozen_clock() -> Iterator[object]:
    from forge_core.clock import FrozenClock

    yield FrozenClock()


def unique(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"

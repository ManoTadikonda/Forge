"""Engine and session management.

The embedded-PostgreSQL path exists so the platform is runnable on a host with no
container runtime and no system PostgreSQL. It is a development convenience only;
``FORGE_DATABASE_URL`` takes precedence whenever it is set.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_embedded_server: Any = None


def embedded_database_url(data_dir: Path | None = None) -> str:
    """Start (or reuse) an embedded PostgreSQL 16 server and return its asyncpg DSN.

    Requires the optional ``pgserver`` dependency. Raises RuntimeError when absent so
    the caller can report a precise remediation step instead of failing obscurely.
    """
    global _embedded_server
    # pgserver logs its whole startup sequence at INFO; it is noise in Forge's own
    # structured output, so it is quietened to warnings here.
    logging.getLogger("pgserver").setLevel(logging.WARNING)
    try:
        import pgserver
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Embedded PostgreSQL requires the 'pgserver' package. Either install it "
            "(pip install pgserver) or set FORGE_DATABASE_URL to an external "
            "PostgreSQL 16 instance."
        ) from exc

    directory = data_dir or Path(
        os.environ.get("FORGE_PG_DATA_DIR", ".forge/pgdata")
    ).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if _embedded_server is None:
        _embedded_server = pgserver.get_server(directory)  # type: ignore[attr-defined]
    # pgserver listens on a Unix socket; asyncpg takes the directory as `host`.
    socket_dir = quote(str(directory), safe="")
    return f"postgresql+asyncpg://postgres@/postgres?host={socket_dir}"


def normalize_database_url(url: str) -> str:
    """Coerce common DSN spellings to the asyncpg driver."""
    if url.startswith("postgresql+asyncpg://") or url.startswith("postgres+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    return url


def create_engine(url: str, *, pool_size: int = 10, max_overflow: int = 10,
                  statement_timeout_ms: int = 30_000, echo: bool = False) -> AsyncEngine:
    dsn = normalize_database_url(url)
    connect_args: dict[str, Any] = {
        # A server-side statement timeout bounds any pathological query and is the
        # backstop for the rule that transactions never wrap long-running work.
        "server_settings": {
            "statement_timeout": str(statement_timeout_ms),
            "application_name": "forge",
        },
    }
    return create_async_engine(
        dsn,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=echo,
        connect_args=connect_args,
    )


def init_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """Create the process-wide engine and sessionmaker."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    _engine = create_engine(url, **kwargs)
    _sessionmaker = async_sessionmaker(
        _engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
    )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine is not initialised; call init_engine() first.")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database engine is not initialised; call init_engine() first.")
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A transactional session. Commits on success, rolls back on any exception."""
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker, _embedded_server
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


__all__ = [
    "create_engine",
    "dispose_engine",
    "embedded_database_url",
    "get_engine",
    "get_sessionmaker",
    "init_engine",
    "normalize_database_url",
    "session_scope",
]

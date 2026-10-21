"""API application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from forge_core.config import Settings, get_settings
from forge_core.db.session import (
    dispose_engine,
    embedded_database_url,
    get_sessionmaker,
    init_engine,
)
from forge_core.logging import configure_logging, get_logger

from forge_api.errors import install_error_handlers
from forge_api.middleware import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from forge_api.ratelimit import build_rate_limiter
from forge_api.routers import (
    admin,
    artifacts,
    audit,
    auth,
    deployments,
    environments,
    health,
    metrics,
    pipelines,
    repositories,
    webhooks,
)

logger = get_logger("forge.api")

API_PREFIX = "/api/v1"

DESCRIPTION = """
Forge is a miniature internal developer platform: it builds application source into
immutable artifacts, deploys them progressively, measures their health, and rolls
back releases that breach their thresholds.

All endpoints are versioned under `/api/v1`. Authentication is a bearer token or a
session cookie; cookie-authenticated writes additionally require a CSRF token.
""".strip()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(
        level=resolved.log_level, fmt=resolved.log_format, service=resolved.service_name
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        url = resolved.database_url or embedded_database_url()
        init_engine(
            url,
            pool_size=resolved.database_pool_size,
            max_overflow=resolved.database_max_overflow,
            statement_timeout_ms=resolved.database_statement_timeout_ms,
        )
        app.state.rate_limiter = build_rate_limiter(resolved.redis_url)
        logger.info(
            "api_started",
            environment=resolved.environment,
            rate_limiter=app.state.rate_limiter.name,
            builder=resolved.builder,
            deploy_adapter=resolved.deploy_adapter,
        )

        from forge_api.bootstrap import run_bootstrap

        maker = get_sessionmaker()
        async with maker() as session, session.begin():
            result = await run_bootstrap(session, resolved)
        logger.info("bootstrap_complete", **result)

        try:
            yield
        finally:
            await app.state.rate_limiter.close()
            await dispose_engine()
            logger.info("api_stopped")

    app = FastAPI(
        title="Forge Control Plane",
        version=resolved.version,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "Forge"},
        license_info={"name": "MIT"},
    )
    app.state.settings = resolved

    # Order matters: security headers wrap the response produced further in, and the
    # request-context middleware must be outermost so every log line is correlated.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        # Explicit origins only; credentials are sent, so a wildcard would be both
        # invalid and unsafe. Settings refuses "*" outside development.
        allow_origins=list(resolved.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token",
                       REQUEST_ID_HEADER, "X-Correlation-ID"],
        expose_headers=[REQUEST_ID_HEADER],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(metrics.router)
    for module in (
        auth, repositories, pipelines, deployments, environments,
        artifacts, audit, admin, webhooks,
    ):
        app.include_router(module.router, prefix=API_PREFIX)

    return app


app = create_app()


def main() -> None:  # pragma: no cover - process entry point
    import os

    import uvicorn

    uvicorn.run(
        "forge_api.main:app",
        host=os.environ.get("FORGE_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("FORGE_API_PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":  # pragma: no cover
    main()

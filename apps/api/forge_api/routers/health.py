"""Liveness and readiness.

``/health`` answers "is this process running" and must stay cheap. ``/ready`` answers
"can it serve requests", which requires the database, so it issues a real query. A
load balancer that cannot distinguish the two will drain a healthy node during a
transient database blip.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from forge_core.db.session import get_sessionmaker
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "status": "healthy",
        "service": settings.service_name,
        "version": settings.version,
        "environment": settings.environment,
    }


@router.get("/ready", summary="Readiness probe")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    try:
        maker = get_sessionmaker()
        async with maker() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        checks["database"] = {"status": "error", "detail": type(exc).__name__}

    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None:
        ping = getattr(limiter, "ping", None)
        reachable = await ping() if ping is not None else True
        checks["rate_limiter"] = {
            "status": "ok" if reachable else "degraded",
            "backend": limiter.name,
        }

    degraded = any(c.get("status") == "error" for c in checks.values())
    if degraded:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "degraded" if degraded else "ready", "checks": checks}

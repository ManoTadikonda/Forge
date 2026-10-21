"""Prometheus exposition."""

from __future__ import annotations

from fastapi import APIRouter, Response
from forge_core.db import queue
from forge_core.db.session import get_sessionmaker
from forge_core.logging import get_logger
from forge_core.metrics import set_queue_depth
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

logger = get_logger("forge.api.metrics")
router = APIRouter(tags=["observability"])


@router.get("/metrics", summary="Prometheus metrics", response_class=Response)
async def metrics() -> Response:
    # Queue depth is a point-in-time fact about the database, so it is refreshed on
    # scrape rather than maintained incrementally (which would drift across replicas).
    try:
        maker = get_sessionmaker()
        async with maker() as session:
            set_queue_depth(await queue.queue_depth(session))
    except Exception:
        # A scrape must never fail because of a transient database problem; the other
        # metrics are still worth returning. Logged so the gap stays visible.
        logger.warning("queue_depth_refresh_failed", exc_info=True)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

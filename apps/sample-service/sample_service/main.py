"""Sample service HTTP application.

Exposes the endpoints Forge's deployment controller probes during a rollout:
``/health``, ``/ready``, ``/version`` and ``/metrics``, plus ``/api/work`` which
produces the request/error/latency signal the health analyser consumes.

Determinism: faulty behaviour is driven by a per-process request counter, not by a
random number generator, so "10% error rate" means exactly one request in ten. A
reproducible demo needs a reproducible fault.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from sample_service.config import SampleConfig

CONFIG = SampleConfig.from_env()
_STARTED_AT = time.monotonic()


class Stats:
    """In-process request accounting, exposed via /metrics and /api/stats.

    The deployment controller reads these counters directly, which gives it a
    measurement source independent of the router's own accounting. Agreement between
    the two is what makes the traffic-split claim verifiable rather than asserted.
    """

    def __init__(self, window: int = 2048) -> None:
        self._lock = Lock()
        self.requests = 0
        self.errors = 0
        self.by_path: dict[str, int] = {}
        self._latencies: deque[float] = deque(maxlen=window)

    def record(self, path: str, status: int, duration_ms: float) -> None:
        with self._lock:
            self.requests += 1
            if status >= 500:
                self.errors += 1
            self.by_path[path] = self.by_path.get(path, 0) + 1
            self._latencies.append(duration_ms)

    def percentile(self, pct: float) -> float:
        with self._lock:
            values = sorted(self._latencies)
        if not values:
            return 0.0
        rank = max(1, min(len(values), round(pct / 100.0 * len(values) + 0.5)))
        return values[rank - 1]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests, errors = self.requests, self.errors
            by_path = dict(self.by_path)
        return {
            "requests": requests,
            "errors": errors,
            "error_rate": (errors / requests) if requests else 0.0,
            "latency_p50_ms": round(self.percentile(50), 3),
            "latency_p95_ms": round(self.percentile(95), 3),
            "latency_p99_ms": round(self.percentile(99), 3),
            "by_path": by_path,
        }


STATS = Stats()
_work_counter = 0
_work_lock = Lock()


def _next_work_index() -> int:
    """Monotonic counter driving deterministic fault injection."""
    global _work_counter
    with _work_lock:
        _work_counter += 1
        return _work_counter


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(
    title="Forge Sample Service",
    version=CONFIG.version,
    description="A small HTTP service that Forge builds, deploys and rolls back.",
    lifespan=lifespan,
)


@app.middleware("http")
async def record_metrics(request: Request, call_next: Any) -> Response:
    started = time.perf_counter()
    try:
        response: Response = await call_next(request)
    except Exception:
        STATS.record(request.url.path, 500, (time.perf_counter() - started) * 1000)
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    STATS.record(request.url.path, response.status_code, duration_ms)
    response.headers["X-Sample-Version"] = CONFIG.version
    response.headers["X-Sample-Release"] = CONFIG.release_id
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.3f}"
    return response


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "forge-sample-service",
        "version": CONFIG.version,
        "release_id": CONFIG.release_id,
        "description": "Sample application built and deployed by Forge.",
        "faulty": CONFIG.is_faulty,
        "endpoints": ["/health", "/ready", "/version", "/api/work", "/api/stats",
                      "/metrics"],
    }


@app.get("/health")
async def health() -> Response:
    """Liveness. Reports failure only when explicitly configured to."""
    if CONFIG.unhealthy:
        return JSONResponse(
            {"status": "unhealthy", "version": CONFIG.version,
             "reason": "SAMPLE_UNHEALTHY is set for this release."},
            status_code=503,
        )
    return JSONResponse({"status": "healthy", "version": CONFIG.version})


@app.get("/ready")
async def ready() -> Response:
    """Readiness. Fails during the configured warm-up window."""
    uptime = time.monotonic() - _STARTED_AT
    if CONFIG.unhealthy:
        return JSONResponse(
            {"status": "not-ready", "version": CONFIG.version,
             "reason": "SAMPLE_UNHEALTHY is set for this release."},
            status_code=503,
        )
    if uptime < CONFIG.startup_delay_seconds:
        return JSONResponse(
            {"status": "starting", "version": CONFIG.version,
             "uptime_seconds": round(uptime, 3)},
            status_code=503,
        )
    return JSONResponse(
        {"status": "ready", "version": CONFIG.version,
         "uptime_seconds": round(uptime, 3)}
    )


@app.get("/version")
async def version() -> dict[str, Any]:
    return {
        "version": CONFIG.version,
        "release_id": CONFIG.release_id,
        "commit": os.environ.get("SAMPLE_COMMIT", ""),
        "image_digest": os.environ.get("SAMPLE_IMAGE_DIGEST", ""),
        "faulty": CONFIG.is_faulty,
    }


@app.get("/api/work")
async def work() -> Response:
    """The measured endpoint.

    Applies the configured latency and error behaviour deterministically so that a
    rollout observes exactly the signal the demo intends.
    """
    index = _next_work_index()

    delay_ms = CONFIG.latency_ms
    if CONFIG.tail_fraction > 0 and CONFIG.tail_latency_ms > 0:
        period = max(1, round(1.0 / CONFIG.tail_fraction))
        if index % period == 0:
            delay_ms += CONFIG.tail_latency_ms
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)

    if CONFIG.error_rate > 0:
        period = max(1, round(1.0 / CONFIG.error_rate))
        if index % period == 0:
            return JSONResponse(
                {"error": "internal_error", "version": CONFIG.version,
                 "request_index": index,
                 "detail": "Injected failure (SAMPLE_ERROR_RATE is set)."},
                status_code=500,
            )

    return JSONResponse(
        {
            "result": "ok",
            "version": CONFIG.version,
            "release_id": CONFIG.release_id,
            "request_index": index,
            "latency_applied_ms": round(delay_ms, 3),
        }
    )


@app.get("/api/stats")
async def stats() -> dict[str, Any]:
    """Counters used by Forge's health analyser."""
    return {
        "version": CONFIG.version,
        "release_id": CONFIG.release_id,
        "uptime_seconds": round(time.monotonic() - _STARTED_AT, 3),
        **STATS.snapshot(),
    }


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus text exposition."""
    snap = STATS.snapshot()
    labels = f'version="{CONFIG.version}",release="{CONFIG.release_id}"'
    lines = [
        "# HELP sample_requests_total Total HTTP requests handled.",
        "# TYPE sample_requests_total counter",
        f"sample_requests_total{{{labels}}} {snap['requests']}",
        "# HELP sample_errors_total HTTP responses with status >= 500.",
        "# TYPE sample_errors_total counter",
        f"sample_errors_total{{{labels}}} {snap['errors']}",
        "# HELP sample_request_latency_ms Observed request latency percentiles.",
        "# TYPE sample_request_latency_ms gauge",
        f'sample_request_latency_ms{{{labels},quantile="0.5"}} {snap["latency_p50_ms"]}',
        f'sample_request_latency_ms{{{labels},quantile="0.95"}} {snap["latency_p95_ms"]}',
        f'sample_request_latency_ms{{{labels},quantile="0.99"}} {snap["latency_p99_ms"]}',
        "# HELP sample_up Whether the service reports itself healthy.",
        "# TYPE sample_up gauge",
        f"sample_up{{{labels}}} {0 if CONFIG.unhealthy else 1}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


def run() -> None:  # pragma: no cover - process entry point
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=CONFIG.port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    run()

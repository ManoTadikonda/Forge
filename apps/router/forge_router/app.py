"""Router process: a public weighted proxy plus a separate admin API.

The two listeners are on different ports on purpose. The admin API mutates routing
weights, so it must not be reachable through the same surface that serves untrusted
public traffic.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from forge_router.state import RoutingTable, Target

TABLE = RoutingTable()
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


class TargetIn(BaseModel):
    release_id: str = Field(min_length=1, max_length=128)
    version: str = Field(default="", max_length=64)
    url: str = Field(min_length=1, max_length=512)


class RouteIn(BaseModel):
    service: str = Field(min_length=1, max_length=64)
    environment: str = Field(min_length=1, max_length=64)
    targets: list[TargetIn] = Field(min_length=1)
    weights: dict[str, int] = Field(default_factory=dict)


class WeightsIn(BaseModel):
    service: str = Field(min_length=1, max_length=64)
    environment: str = Field(min_length=1, max_length=64)
    weights: dict[str, int]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        follow_redirects=False,
    )
    try:
        yield
    finally:
        await app.state.client.aclose()


# --------------------------------------------------------------------------- admin

admin_app = FastAPI(
    title="Forge Router Admin",
    version="0.1.0",
    description="Controls traffic weights between releases.",
)


@admin_app.get("/health")
async def admin_health() -> dict[str, Any]:
    return {"status": "healthy", "routes": len(TABLE.all())}


@admin_app.get("/routes")
async def list_routes() -> dict[str, Any]:
    return {
        "routes": [
            {
                "service": route.service,
                "environment": route.environment,
                "targets": [
                    {"release_id": t.release_id, "version": t.version, "url": t.url}
                    for t in route.targets.values()
                ],
                "weights": route.balancer.weights(),
                "stats": route.balancer.stats(),
            }
            for route in TABLE.all()
        ]
    }


@admin_app.put("/routes")
async def upsert_route(payload: Annotated[RouteIn, Body()]) -> dict[str, Any]:
    targets = {
        t.release_id: Target(t.release_id, t.version, t.url.rstrip("/")) for t in payload.targets
    }
    weights = payload.weights or {rid: (100 if i == 0 else 0) for i, rid in enumerate(targets)}
    unknown = set(weights) - set(targets)
    if unknown:
        raise HTTPException(422, f"Weights reference unknown releases: {sorted(unknown)}")
    route = TABLE.upsert(payload.service, payload.environment, targets, weights)
    return {
        "service": route.service,
        "environment": route.environment,
        "weights": route.balancer.weights(),
    }


@admin_app.post("/weights")
async def set_weights(payload: Annotated[WeightsIn, Body()]) -> dict[str, Any]:
    total = sum(payload.weights.values())
    if total != 100:
        raise HTTPException(422, f"Weights must sum to 100, received {total}.")
    try:
        route = TABLE.set_weights(payload.service, payload.environment, payload.weights)
    except KeyError as exc:
        raise HTTPException(422, str(exc)) from exc
    if route is None:
        raise HTTPException(404, "No such route.")
    return {
        "service": route.service,
        "environment": route.environment,
        "weights": route.balancer.weights(),
    }


@admin_app.get("/stats")
async def route_stats(service: str, environment: str) -> dict[str, Any]:
    route = TABLE.get(service, environment)
    if route is None:
        raise HTTPException(404, "No such route.")
    return {
        "service": service,
        "environment": environment,
        "weights": route.balancer.weights(),
        "stats": route.balancer.stats(),
    }


@admin_app.post("/stats/reset")
async def reset_stats(payload: Annotated[dict[str, str], Body()]) -> dict[str, Any]:
    route = TABLE.get(payload.get("service", ""), payload.get("environment", ""))
    if route is None:
        raise HTTPException(404, "No such route.")
    route.balancer.reset_counters()
    return {"reset": True}


@admin_app.delete("/routes/{service}/{environment}/targets/{release_id}")
async def remove_target(service: str, environment: str, release_id: str) -> dict[str, Any]:
    removed = TABLE.remove_target(service, environment, release_id)
    if not removed:
        raise HTTPException(404, "No such release on this route.")
    return {"removed": release_id}


@admin_app.delete("/routes/{service}/{environment}")
async def delete_route(service: str, environment: str) -> dict[str, Any]:
    return {"deleted": TABLE.delete(service, environment)}


# ---------------------------------------------------------------------------- proxy

proxy_app = FastAPI(
    title="Forge Router",
    version="0.1.0",
    description="Weighted reverse proxy for Forge releases.",
    lifespan=_lifespan,
)


@proxy_app.get("/_router/health")
async def proxy_health() -> dict[str, Any]:
    return {"status": "healthy"}


@proxy_app.api_route(
    "/{service}/{environment}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(service: str, environment: str, path: str, request: Request) -> Response:
    """Forward a request to a release chosen by weight.

    The selected release id and the weight that produced the choice are echoed in
    response headers, so the traffic split is observable from the client side as
    well as from the router's counters.
    """
    route = TABLE.get(service, environment)
    if route is None:
        return JSONResponse(
            {"error": "no_route", "detail": f"No route registered for {service}/{environment}."},
            status_code=503,
        )

    release_id = route.balancer.select()
    if release_id is None or release_id not in route.targets:
        return JSONResponse(
            {
                "error": "no_healthy_target",
                "detail": "No release currently carries traffic for this route.",
            },
            status_code=503,
        )

    target = route.targets[release_id]
    url = f"{target.url}/{path}" if path else target.url
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    headers["X-Forwarded-For"] = request.client.host if request.client else "unknown"
    headers["X-Forge-Release"] = release_id

    client: httpx.AsyncClient = proxy_app.state.client
    started = time.perf_counter()
    try:
        body = await request.body()
        upstream = await client.request(
            request.method,
            url,
            content=body or None,
            headers=headers,
            params=dict(request.query_params),
        )
    except httpx.RequestError as exc:
        # An unreachable backend is a 502 and is counted as an error against that
        # release, which is exactly the signal a failing canary should produce.
        route.balancer.record(release_id, error=True)
        return JSONResponse(
            {
                "error": "upstream_unavailable",
                "release_id": release_id,
                "detail": type(exc).__name__,
            },
            status_code=502,
            headers={"X-Forge-Release": release_id, "X-Forge-Version": target.version},
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    route.balancer.record(release_id, error=upstream.status_code >= 500)

    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    response_headers["X-Forge-Release"] = release_id
    response_headers["X-Forge-Version"] = target.version
    response_headers["X-Forge-Weight"] = str(route.balancer.weights().get(release_id, 0))
    response_headers["X-Forge-Upstream-Ms"] = f"{elapsed_ms:.3f}"
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


@proxy_app.get("/_router/metrics")
async def proxy_metrics() -> Response:
    lines = [
        "# HELP forge_router_requests_total Requests proxied per release.",
        "# TYPE forge_router_requests_total counter",
    ]
    for route in TABLE.all():
        for release_id, stat in route.balancer.stats().items():
            labels = (
                f'service="{route.service}",environment="{route.environment}",'
                f'release="{release_id}"'
            )
            lines.append(f"forge_router_requests_total{{{labels}}} {stat['requests']}")
            lines.append(f"forge_router_errors_total{{{labels}}} {stat['errors']}")
            lines.append(f"forge_router_weight{{{labels}}} {stat['weight']}")
    return PlainTextResponse("\n".join(lines) + "\n")


async def serve() -> None:  # pragma: no cover - process entry point
    """Run the proxy and admin listeners concurrently in one process."""
    import uvicorn

    proxy_port = int(os.environ.get("FORGE_ROUTER_PORT", "8098"))
    admin_port = int(os.environ.get("FORGE_ROUTER_ADMIN_PORT", "8099"))
    host = os.environ.get("FORGE_ROUTER_HOST", "127.0.0.1")

    proxy_server = uvicorn.Server(
        uvicorn.Config(proxy_app, host=host, port=proxy_port, log_level="warning")
    )
    admin_server = uvicorn.Server(
        uvicorn.Config(admin_app, host=host, port=admin_port, log_level="warning")
    )
    await asyncio.gather(proxy_server.serve(), admin_server.serve())


def main() -> None:  # pragma: no cover
    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover
    main()

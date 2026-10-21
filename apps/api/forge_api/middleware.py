"""Request middleware: correlation ids, structured access logs, security headers."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from forge_core.logging import bind_context, get_logger, reset_context
from forge_core.metrics import observe_http_request
from starlette.middleware.base import BaseHTTPMiddleware

logger = get_logger("forge.api.access")

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"

#: Applied to every response. The API serves JSON to a separate SPA origin, so the
#: CSP is restrictive: no scripts, no framing, no plugins.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "Cache-Control": "no-store",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, binds logging context and emits one access log line."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Accept a caller-supplied id so a trace can span the UI and the API, but
        # bound its length — it ends up in logs.
        incoming = (request.headers.get(REQUEST_ID_HEADER) or "")[:64]
        request_id = incoming or str(uuid.uuid4())
        correlation_id = (
            request.headers.get(CORRELATION_ID_HEADER) or request_id
        )[:64]
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        tokens = bind_context(request_id=request_id, correlation_id=correlation_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            route = request.scope.get("route")
            template = getattr(route, "path", request.url.path)
            observe_http_request(
                method=request.method, path=template,
                status=status_code, duration_seconds=duration_ms / 1000,
            )
            logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                route=template,
                status=status_code,
                duration_ms=round(duration_ms, 2),
                client=request.client.host if request.client else "",
            )
            reset_context(tokens)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        request_id = getattr(request.state, "request_id", "")
        if request_id:
            response.headers[REQUEST_ID_HEADER] = request_id
        return response


__all__ = [
    "CORRELATION_ID_HEADER",
    "REQUEST_ID_HEADER",
    "SECURITY_HEADERS",
    "RequestContextMiddleware",
    "SecurityHeadersMiddleware",
]

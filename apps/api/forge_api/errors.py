"""Uniform API error envelope.

Every error response has the same shape. Internal details — stack traces, SQL,
credentials — never cross the boundary: unexpected exceptions are logged in full
server-side and reported to the client as an opaque message plus the request id,
which is the handle an operator uses to find the log entry.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from forge_core.errors import ForgeError
from forge_core.logging import get_logger
from forge_core.redaction import redact_object
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = get_logger("forge.api.errors")


def error_body(
    code: str, message: str, *, request_id: str = "", details: Any = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message},
        "request_id": request_id,
    }
    if details:
        cleaned = redact_object(details)
        body["error"]["details"] = cleaned
    return body


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ForgeError)
    async def _forge_error(request: Request, exc: ForgeError) -> JSONResponse:
        logger.info(
            "domain_error",
            code=exc.code,
            status=exc.http_status,
            message=exc.message,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(
                exc.code,
                exc.message,
                request_id=_request_id(request),
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field errors are safe to return: they describe the client's own request.
        details = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())[1:]),
                "message": err.get("msg", "invalid value"),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_body(
                "validation_error",
                "The request body or parameters are invalid.",
                request_id=_request_id(request),
                details=details,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "authentication_required",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail), request_id=_request_id(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Full detail to the log, nothing to the client but a correlation id.
        logger.exception(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body(
                "internal_error",
                "An internal error occurred. Quote the request id when reporting it.",
                request_id=_request_id(request),
            ),
        )


__all__ = ["error_body", "install_error_handlers"]

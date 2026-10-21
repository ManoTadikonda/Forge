"""Structured JSON logging with correlation context and automatic redaction.

Every log record carries whatever identifiers are bound to the current context
(request id, pipeline id, deployment id), and every record passes through the
redactor before it is emitted.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

from forge_core.redaction import redact_object

_request_id: ContextVar[str | None] = ContextVar("forge_request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar("forge_correlation_id", default=None)
_pipeline_id: ContextVar[str | None] = ContextVar("forge_pipeline_id", default=None)
_deployment_id: ContextVar[str | None] = ContextVar("forge_deployment_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("forge_job_id", default=None)
_actor: ContextVar[str | None] = ContextVar("forge_actor", default=None)

_CONTEXT_VARS: dict[str, ContextVar[str | None]] = {
    "request_id": _request_id,
    "correlation_id": _correlation_id,
    "pipeline_id": _pipeline_id,
    "deployment_id": _deployment_id,
    "job_id": _job_id,
    "actor": _actor,
}


def bind_context(**values: str | None) -> dict[str, object]:
    """Bind correlation identifiers to the current context.

    Returns reset tokens keyed by field name; pass them to :func:`reset_context`.
    """
    tokens: dict[str, object] = {}
    for key, value in values.items():
        var = _CONTEXT_VARS.get(key)
        if var is not None:
            tokens[key] = var.set(value)
    return tokens


def reset_context(tokens: dict[str, object]) -> None:
    for key, token in tokens.items():
        var = _CONTEXT_VARS.get(key)
        if var is not None:
            var.reset(token)  # type: ignore[arg-type]


def clear_context() -> None:
    for var in _CONTEXT_VARS.values():
        var.set(None)


def _inject_context(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key, var in _CONTEXT_VARS.items():
        value = var.get()
        if value is not None and key not in event_dict:
            event_dict[key] = value
    return event_dict


def _redact_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    result = redact_object(dict(event_dict))
    assert isinstance(result, dict)
    return result


def configure_logging(
    *, level: str = "INFO", fmt: str = "json", service: str = "forge"
) -> None:
    """Install the structlog + stdlib logging pipeline. Idempotent."""
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_processor,
    ]

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(processors=[_redact_processor, renderer])
    )
    root.addHandler(handler)
    root.setLevel(logging.getLevelNamesMapping().get(level.upper(), logging.INFO))

    # Uvicorn duplicates access logs we already emit via middleware.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("uvicorn.error", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str = "forge") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


__all__ = [
    "bind_context",
    "clear_context",
    "configure_logging",
    "get_logger",
    "reset_context",
]

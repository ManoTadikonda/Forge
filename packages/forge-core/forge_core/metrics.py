"""Prometheus metrics.

Registered on the default registry so one ``/metrics`` endpoint exposes everything.
Label cardinality is kept deliberately low: HTTP metrics use the *route template*
(``/api/v1/pipelines/{pipeline_id}``), never the concrete path, so one object per
route rather than one per id.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "forge_http_requests_total",
    "HTTP requests handled by the control plane.",
    ("method", "route", "status"),
)
HTTP_LATENCY = Histogram(
    "forge_http_request_duration_seconds",
    "HTTP request latency.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

QUEUE_DEPTH = Gauge(
    "forge_queue_depth",
    "Jobs in the durable queue, by state.",
    ("state",),
)
JOB_DURATION = Histogram(
    "forge_job_duration_seconds",
    "Job execution time.",
    ("kind", "outcome"),
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800),
)
JOB_FAILURES = Counter(
    "forge_job_failures_total",
    "Job failures, by kind and terminal state.",
    ("kind", "state"),
)

PIPELINE_OUTCOMES = Counter(
    "forge_pipeline_outcomes_total",
    "Pipeline runs by terminal state.",
    ("state",),
)
DEPLOYMENT_OUTCOMES = Counter(
    "forge_deployment_outcomes_total",
    "Deployments by terminal state.",
    ("state",),
)
ROLLBACKS = Counter(
    "forge_rollbacks_total",
    "Automated rollbacks, by whether recovery was verified.",
    ("verified",),
)
ACTIVE_DEPLOYMENTS = Gauge(
    "forge_active_deployments",
    "Deployments currently in a non-terminal state.",
)


def observe_http_request(
    *, method: str, path: str, status: int, duration_seconds: float
) -> None:
    HTTP_REQUESTS.labels(method=method, route=path, status=str(status)).inc()
    HTTP_LATENCY.labels(method=method, route=path).observe(duration_seconds)


def record_pipeline_outcome(state: str) -> None:
    PIPELINE_OUTCOMES.labels(state=state).inc()


def record_deployment_outcome(state: str) -> None:
    DEPLOYMENT_OUTCOMES.labels(state=state).inc()


def record_rollback(*, verified: bool) -> None:
    ROLLBACKS.labels(verified=str(verified).lower()).inc()


def set_queue_depth(counts: dict[str, int]) -> None:
    for state, value in counts.items():
        QUEUE_DEPTH.labels(state=state).set(value)


__all__ = [
    "ACTIVE_DEPLOYMENTS",
    "DEPLOYMENT_OUTCOMES",
    "HTTP_LATENCY",
    "HTTP_REQUESTS",
    "JOB_DURATION",
    "JOB_FAILURES",
    "PIPELINE_OUTCOMES",
    "QUEUE_DEPTH",
    "ROLLBACKS",
    "observe_http_request",
    "record_deployment_outcome",
    "record_pipeline_outcome",
    "record_rollback",
    "set_queue_depth",
]

"""Health analysis for canary rollouts.

Design rules, in priority order:

1. **Unknown is not healthy.** A verdict of HEALTHY requires positive evidence:
   at least ``min_samples`` observations, none of them stale. Absence of telemetry
   yields UNKNOWN, which never promotes.
2. **Thresholds are absolute, with an optional comparison to stable.** A candidate
   breaches if it exceeds the configured error-rate or p95 latency ceiling. When a
   stable baseline exists, a large *relative* regression also breaches, which catches
   services whose absolute ceiling is generous.
3. **Every verdict carries its evidence.** The measurements behind a decision are
   persisted so an operator can audit why a rollback happened.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from forge_core.enums import HealthVerdict


@dataclass(frozen=True, slots=True)
class HealthSample:
    """One observation scraped from a running release."""

    observed_at: datetime
    requests: int
    errors: int
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    healthy_endpoint: bool = True
    ready_endpoint: bool = True


@dataclass(frozen=True, slots=True)
class HealthThresholds:
    min_samples: int = 20
    max_error_rate: float = 0.05
    max_p95_latency_ms: float = 750.0
    staleness_seconds: int = 60
    #: Candidate p95 may not exceed stable p95 by more than this factor.
    max_latency_regression_factor: float = 3.0
    #: Candidate error rate may not exceed stable error rate by more than this.
    max_error_rate_regression: float = 0.05


@dataclass(frozen=True, slots=True)
class HealthAssessment:
    verdict: HealthVerdict
    reason: str
    requests: int = 0
    errors: int = 0
    error_rate: float = 0.0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    breaches: tuple[str, ...] = field(default=())

    @property
    def is_healthy(self) -> bool:
        return self.verdict is HealthVerdict.HEALTHY

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": str(self.verdict),
            "reason": self.reason,
            "requests": self.requests,
            "errors": self.errors,
            "error_rate": round(self.error_rate, 6),
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_p99_ms": self.latency_p99_ms,
            "breaches": list(self.breaches),
        }


def _percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile. Returns None for an empty input."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(pct / 100.0 * len(ordered) + 0.5)))
    return ordered[rank - 1]


def analyze(
    samples: Sequence[HealthSample],
    thresholds: HealthThresholds,
    *,
    now: datetime,
    baseline: HealthAssessment | None = None,
) -> HealthAssessment:
    """Produce a verdict for one release from its observations.

    ``baseline`` is the concurrently-measured stable release, when one exists.
    """
    if not samples:
        return HealthAssessment(
            HealthVerdict.UNKNOWN,
            "No telemetry received from the candidate release.",
        )

    cutoff = now - timedelta(seconds=thresholds.staleness_seconds)
    fresh = [s for s in samples if s.observed_at >= cutoff]
    if not fresh:
        newest = max(s.observed_at for s in samples)
        age = int((now - newest).total_seconds())
        return HealthAssessment(
            HealthVerdict.UNKNOWN,
            f"All telemetry is stale; newest sample is {age}s old "
            f"(limit {thresholds.staleness_seconds}s).",
        )

    requests = sum(s.requests for s in fresh)
    errors = sum(s.errors for s in fresh)
    error_rate = (errors / requests) if requests else 0.0

    p50 = _percentile([s.latency_p50_ms for s in fresh if s.latency_p50_ms is not None], 50)
    p95 = _percentile([s.latency_p95_ms for s in fresh if s.latency_p95_ms is not None], 95)
    p99 = _percentile([s.latency_p99_ms for s in fresh if s.latency_p99_ms is not None], 99)

    # A failing liveness/readiness probe is decisive regardless of sample volume:
    # it is a direct statement from the application that it is not serving.
    unhealthy_probe = [s for s in fresh if not s.healthy_endpoint or not s.ready_endpoint]
    if unhealthy_probe:
        return HealthAssessment(
            HealthVerdict.UNHEALTHY,
            f"{len(unhealthy_probe)} of {len(fresh)} probes reported the release "
            f"as not healthy/ready.",
            requests,
            errors,
            error_rate,
            p50,
            p95,
            p99,
            ("probe_failed",),
        )

    if requests < thresholds.min_samples:
        return HealthAssessment(
            HealthVerdict.UNKNOWN,
            f"Insufficient traffic to judge: {requests} requests observed, "
            f"{thresholds.min_samples} required.",
            requests,
            errors,
            error_rate,
            p50,
            p95,
            p99,
        )

    breaches: list[str] = []
    reasons: list[str] = []

    if error_rate > thresholds.max_error_rate:
        breaches.append("error_rate")
        reasons.append(
            f"error rate {error_rate:.2%} exceeds ceiling {thresholds.max_error_rate:.2%}"
        )
    if p95 is not None and p95 > thresholds.max_p95_latency_ms:
        breaches.append("latency_p95")
        reasons.append(
            f"p95 latency {p95:.0f}ms exceeds ceiling {thresholds.max_p95_latency_ms:.0f}ms"
        )

    if baseline is not None and baseline.requests >= thresholds.min_samples:
        if (
            baseline.latency_p95_ms
            and p95 is not None
            and p95 > baseline.latency_p95_ms * thresholds.max_latency_regression_factor
        ):
            breaches.append("latency_regression")
            reasons.append(
                f"p95 latency {p95:.0f}ms regressed more than "
                f"{thresholds.max_latency_regression_factor:g}x against stable "
                f"({baseline.latency_p95_ms:.0f}ms)"
            )
        if error_rate - baseline.error_rate > thresholds.max_error_rate_regression:
            breaches.append("error_rate_regression")
            reasons.append(
                f"error rate {error_rate:.2%} regressed against stable ({baseline.error_rate:.2%})"
            )

    if breaches:
        return HealthAssessment(
            HealthVerdict.UNHEALTHY,
            "Health thresholds breached: " + "; ".join(reasons) + ".",
            requests,
            errors,
            error_rate,
            p50,
            p95,
            p99,
            tuple(dict.fromkeys(breaches)),
        )

    return HealthAssessment(
        HealthVerdict.HEALTHY,
        f"{requests} requests observed, error rate {error_rate:.2%}"
        + (f", p95 {p95:.0f}ms" if p95 is not None else "")
        + " — within thresholds.",
        requests,
        errors,
        error_rate,
        p50,
        p95,
        p99,
    )


__all__ = [
    "HealthAssessment",
    "HealthSample",
    "HealthThresholds",
    "analyze",
]

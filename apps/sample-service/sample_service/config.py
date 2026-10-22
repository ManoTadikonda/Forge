"""Sample service configuration.

Faulty behaviour is **only** ever enabled by explicit configuration. There is no
random or time-based degradation: a release misbehaves because the operator asked
it to, which is what makes the rollback demonstration reproducible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class SampleConfig:
    version: str = "1.0.0"
    release_id: str = "local"
    port: int = 8080

    #: Fixed latency added to every /api/work response, in milliseconds.
    latency_ms: float = 0.0
    #: Extra latency applied to a fraction of requests, to shape a tail.
    tail_latency_ms: float = 0.0
    tail_fraction: float = 0.0
    #: Fraction of /api/work requests answered with HTTP 500. 0.0 disables.
    error_rate: float = 0.0
    #: When true, /health and /ready report failure. Used for probe-failure demos.
    unhealthy: bool = False
    #: Delay before /ready starts succeeding, simulating slow warm-up.
    startup_delay_seconds: float = 0.0

    @classmethod
    def from_env(cls) -> SampleConfig:
        return cls(
            version=os.environ.get("SAMPLE_VERSION", "1.0.0"),
            release_id=os.environ.get("SAMPLE_RELEASE_ID", "local"),
            port=_int_env("PORT", 8080),
            latency_ms=max(0.0, _float_env("SAMPLE_LATENCY_MS", 0.0)),
            tail_latency_ms=max(0.0, _float_env("SAMPLE_TAIL_LATENCY_MS", 0.0)),
            tail_fraction=min(1.0, max(0.0, _float_env("SAMPLE_TAIL_FRACTION", 0.0))),
            error_rate=min(1.0, max(0.0, _float_env("SAMPLE_ERROR_RATE", 0.0))),
            unhealthy=os.environ.get("SAMPLE_UNHEALTHY", "").lower() in ("1", "true", "yes"),
            startup_delay_seconds=max(0.0, _float_env("SAMPLE_STARTUP_DELAY_SECONDS", 0.0)),
        )

    @property
    def is_faulty(self) -> bool:
        """True when this release is deliberately configured to misbehave."""
        return (
            self.error_rate > 0.0
            or self.latency_ms > 0.0
            or self.tail_latency_ms > 0.0
            or self.unhealthy
        )


__all__ = ["SampleConfig"]

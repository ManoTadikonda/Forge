"""Health analysis: the logic that decides whether a release is safe to promote."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from forge_core.enums import HealthVerdict
from forge_core.health import HealthSample, HealthThresholds, analyze

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def sample(**kwargs: object) -> HealthSample:
    defaults: dict[str, object] = {
        "observed_at": NOW,
        "requests": 100,
        "errors": 0,
        "latency_p50_ms": 10.0,
        "latency_p95_ms": 50.0,
        "latency_p99_ms": 90.0,
        "healthy_endpoint": True,
        "ready_endpoint": True,
    }
    defaults.update(kwargs)
    return HealthSample(**defaults)  # type: ignore[arg-type]


class TestUnknownIsNeverHealthy:
    """The central safety property: absence of evidence is not evidence of health."""

    def test_no_samples_is_unknown(self) -> None:
        result = analyze([], HealthThresholds(), now=NOW)
        assert result.verdict is HealthVerdict.UNKNOWN
        assert not result.is_healthy

    def test_insufficient_samples_is_unknown(self) -> None:
        result = analyze([sample(requests=5, errors=0)], HealthThresholds(min_samples=20), now=NOW)
        assert result.verdict is HealthVerdict.UNKNOWN
        assert "Insufficient traffic" in result.reason

    def test_stale_telemetry_is_unknown(self) -> None:
        old = sample(observed_at=NOW - timedelta(seconds=600))
        result = analyze([old], HealthThresholds(staleness_seconds=60), now=NOW)
        assert result.verdict is HealthVerdict.UNKNOWN
        assert "stale" in result.reason.lower()

    def test_zero_errors_with_no_traffic_is_not_healthy(self) -> None:
        """0/0 is a 0% error rate arithmetically, but says nothing about health."""
        result = analyze([sample(requests=0, errors=0)], HealthThresholds(min_samples=20), now=NOW)
        assert result.verdict is HealthVerdict.UNKNOWN


class TestThresholdBreaches:
    def test_error_rate_above_ceiling_is_unhealthy(self) -> None:
        result = analyze(
            [sample(requests=100, errors=50)],
            HealthThresholds(min_samples=20, max_error_rate=0.05),
            now=NOW,
        )
        assert result.verdict is HealthVerdict.UNHEALTHY
        assert "error_rate" in result.breaches
        assert result.error_rate == pytest.approx(0.5)

    def test_error_rate_exactly_at_ceiling_is_healthy(self) -> None:
        """The ceiling is inclusive; only exceeding it breaches."""
        result = analyze(
            [sample(requests=100, errors=5)],
            HealthThresholds(min_samples=20, max_error_rate=0.05),
            now=NOW,
        )
        assert result.verdict is HealthVerdict.HEALTHY

    def test_p95_latency_above_ceiling_is_unhealthy(self) -> None:
        result = analyze(
            [sample(latency_p95_ms=5000.0)],
            HealthThresholds(min_samples=20, max_p95_latency_ms=750.0),
            now=NOW,
        )
        assert result.verdict is HealthVerdict.UNHEALTHY
        assert "latency_p95" in result.breaches

    def test_multiple_breaches_are_all_reported(self) -> None:
        result = analyze(
            [sample(requests=100, errors=90, latency_p95_ms=9000.0)],
            HealthThresholds(min_samples=20),
            now=NOW,
        )
        assert set(result.breaches) >= {"error_rate", "latency_p95"}

    def test_failing_probe_is_decisive_regardless_of_sample_count(self) -> None:
        """A service saying "I am not ready" needs no statistical support."""
        result = analyze(
            [sample(requests=1, healthy_endpoint=False)],
            HealthThresholds(min_samples=1000),
            now=NOW,
        )
        assert result.verdict is HealthVerdict.UNHEALTHY
        assert "probe_failed" in result.breaches

    def test_failing_readiness_probe_is_unhealthy(self) -> None:
        result = analyze([sample(ready_endpoint=False)], HealthThresholds(min_samples=20), now=NOW)
        assert result.verdict is HealthVerdict.UNHEALTHY


class TestBaselineComparison:
    def test_latency_regression_against_stable_breaches(self) -> None:
        baseline = analyze([sample(latency_p95_ms=50.0)], HealthThresholds(min_samples=20), now=NOW)
        # Below the absolute ceiling, but 10x worse than stable.
        result = analyze(
            [sample(latency_p95_ms=500.0)],
            HealthThresholds(
                min_samples=20, max_p95_latency_ms=5000.0, max_latency_regression_factor=3.0
            ),
            now=NOW,
            baseline=baseline,
        )
        assert result.verdict is HealthVerdict.UNHEALTHY
        assert "latency_regression" in result.breaches

    def test_error_rate_regression_against_stable_breaches(self) -> None:
        baseline = analyze(
            [sample(requests=100, errors=0)], HealthThresholds(min_samples=20), now=NOW
        )
        result = analyze(
            [sample(requests=100, errors=30)],
            HealthThresholds(min_samples=20, max_error_rate=0.9, max_error_rate_regression=0.05),
            now=NOW,
            baseline=baseline,
        )
        assert result.verdict is HealthVerdict.UNHEALTHY
        assert "error_rate_regression" in result.breaches

    def test_baseline_with_too_little_traffic_is_ignored(self) -> None:
        """A noisy baseline must not cause spurious regression verdicts."""
        baseline = analyze(
            [sample(requests=2, latency_p95_ms=1.0)], HealthThresholds(min_samples=20), now=NOW
        )
        result = analyze(
            [sample(latency_p95_ms=100.0)],
            HealthThresholds(min_samples=20, max_p95_latency_ms=5000.0),
            now=NOW,
            baseline=baseline,
        )
        assert result.verdict is HealthVerdict.HEALTHY


class TestHealthyPath:
    def test_clean_metrics_are_healthy(self) -> None:
        result = analyze(
            [sample(requests=100, errors=1)], HealthThresholds(min_samples=20), now=NOW
        )
        assert result.verdict is HealthVerdict.HEALTHY
        assert result.is_healthy
        assert result.requests == 100

    def test_mixed_fresh_and_stale_uses_only_fresh(self) -> None:
        fresh = sample(requests=100, errors=0)
        stale = sample(observed_at=NOW - timedelta(seconds=900), requests=100, errors=100)
        result = analyze(
            [stale, fresh], HealthThresholds(min_samples=20, staleness_seconds=60), now=NOW
        )
        assert result.verdict is HealthVerdict.HEALTHY
        assert result.requests == 100
        assert result.errors == 0

    def test_assessment_serialises_its_evidence(self) -> None:
        result = analyze(
            [sample(requests=100, errors=50)], HealthThresholds(min_samples=20), now=NOW
        )
        payload = result.as_dict()
        assert payload["verdict"] == "UNHEALTHY"
        assert payload["requests"] == 100
        assert payload["errors"] == 50
        assert payload["breaches"] == ["error_rate"]

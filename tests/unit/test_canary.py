"""Canary progression decisions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge_core.canary import CanaryAction, CanaryPlan, decide
from forge_core.health import HealthAssessment, HealthSample, HealthThresholds, analyze

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PLAN = CanaryPlan((10, 25, 50, 100), step_seconds=30)


def healthy() -> HealthAssessment:
    return analyze(
        [HealthSample(NOW, requests=100, errors=0, latency_p95_ms=10.0)],
        HealthThresholds(min_samples=20),
        now=NOW,
    )


def unhealthy() -> HealthAssessment:
    return analyze(
        [HealthSample(NOW, requests=100, errors=90, latency_p95_ms=10.0)],
        HealthThresholds(min_samples=20),
        now=NOW,
    )


def unknown() -> HealthAssessment:
    return analyze([], HealthThresholds(), now=NOW)


class TestPlanValidation:
    def test_rejects_empty_plan(self) -> None:
        with pytest.raises(ValueError, match="at least one step"):
            CanaryPlan((), 10)

    def test_rejects_descending_steps(self) -> None:
        with pytest.raises(ValueError, match="ascending"):
            CanaryPlan((50, 25, 100), 10)

    def test_rejects_plan_not_finishing_at_100(self) -> None:
        with pytest.raises(ValueError, match="finish at 100"):
            CanaryPlan((10, 50), 10)

    def test_rejects_out_of_range_weights(self) -> None:
        with pytest.raises(ValueError, match=r"within 1\.\.100"):
            CanaryPlan((0, 100), 10)

    def test_single_step_plan_is_valid(self) -> None:
        plan = CanaryPlan((100,), 5)
        assert plan.total_steps == 1
        assert plan.is_final(0)

    def test_weight_at_clamps_out_of_range_index(self) -> None:
        assert PLAN.weight_at(-5) == 10
        assert PLAN.weight_at(99) == 100


class TestDecisions:
    def test_holds_while_window_has_not_elapsed(self) -> None:
        decision = decide(PLAN, step_index=0, seconds_in_step=5, assessment=healthy())
        assert decision.action is CanaryAction.HOLD
        assert decision.next_weight == 10

    def test_advances_once_window_elapses(self) -> None:
        decision = decide(PLAN, step_index=0, seconds_in_step=30, assessment=healthy())
        assert decision.action is CanaryAction.ADVANCE
        assert decision.next_weight == 25

    def test_promotes_at_final_step(self) -> None:
        decision = decide(PLAN, step_index=3, seconds_in_step=30, assessment=healthy())
        assert decision.action is CanaryAction.PROMOTE

    def test_aborts_immediately_on_unhealthy_without_waiting(self) -> None:
        """A breach must not wait out the observation window."""
        decision = decide(PLAN, step_index=0, seconds_in_step=0, assessment=unhealthy())
        assert decision.action is CanaryAction.ABORT
        assert "error rate" in decision.reason

    def test_unknown_holds_inside_the_window(self) -> None:
        decision = decide(PLAN, step_index=0, seconds_in_step=5, assessment=unknown())
        assert decision.action is CanaryAction.HOLD

    def test_unknown_aborts_once_the_window_expires(self) -> None:
        """Silence must not stall a rollout forever, and must never promote."""
        decision = decide(PLAN, step_index=0, seconds_in_step=31, assessment=unknown())
        assert decision.action is CanaryAction.ABORT
        assert "could not be confirmed" in decision.reason

    def test_unknown_can_be_configured_to_hold_indefinitely(self) -> None:
        decision = decide(
            PLAN,
            step_index=0,
            seconds_in_step=999,
            assessment=unknown(),
            abort_on_unknown_after_window=False,
        )
        assert decision.action is CanaryAction.HOLD

    def test_unknown_never_promotes(self) -> None:
        for seconds in (0, 15, 30, 1000):
            decision = decide(PLAN, step_index=3, seconds_in_step=seconds, assessment=unknown())
            assert decision.action is not CanaryAction.PROMOTE

    def test_full_healthy_progression_reaches_promote(self) -> None:
        index, actions = 0, []
        for _ in range(10):
            decision = decide(PLAN, step_index=index, seconds_in_step=30, assessment=healthy())
            actions.append(decision.action)
            if decision.action is CanaryAction.PROMOTE:
                break
            index = decision.next_index
        assert actions == [
            CanaryAction.ADVANCE,
            CanaryAction.ADVANCE,
            CanaryAction.ADVANCE,
            CanaryAction.PROMOTE,
        ]


class TestProbeSizing:
    """Probe volume must scale with weight or low-weight steps are always UNKNOWN."""

    def test_candidate_clears_min_samples_at_every_weight(self) -> None:
        from forge_worker.controller import _probe_count_for

        for weight in (1, 10, 25, 50, 100):
            count = _probe_count_for(weight, min_samples=20)
            assert count * weight / 100 >= 20, f"weight={weight} gives too few samples"

    def test_probe_count_is_bounded(self) -> None:
        from forge_worker.controller import _MAX_PROBE_REQUESTS, _probe_count_for

        assert _probe_count_for(1, min_samples=1000) <= _MAX_PROBE_REQUESTS

    def test_zero_weight_falls_back_to_min_samples(self) -> None:
        from forge_worker.controller import _probe_count_for

        assert _probe_count_for(0, min_samples=20) == 20

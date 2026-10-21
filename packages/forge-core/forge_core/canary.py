"""Canary rollout progression.

The plan is computed up front and is pure, so the controller's advance/abort decision
is fully unit-testable without any infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from forge_core.enums import HealthVerdict
from forge_core.health import HealthAssessment


class CanaryAction(StrEnum):
    #: Keep observing at the current weight; the window has not elapsed.
    HOLD = "HOLD"
    #: Health is good and the window elapsed; move to the next weight.
    ADVANCE = "ADVANCE"
    #: Final weight reached with a healthy verdict; promote the candidate.
    PROMOTE = "PROMOTE"
    #: Health breached; abort and roll back.
    ABORT = "ABORT"


@dataclass(frozen=True, slots=True)
class CanaryPlan:
    """An ordered set of traffic weights, ending at 100%."""

    steps: tuple[int, ...]
    step_seconds: int

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("A canary plan requires at least one step")
        if list(self.steps) != sorted(self.steps):
            raise ValueError("Canary steps must be ascending")
        if self.steps[-1] != 100:
            raise ValueError("Canary steps must finish at 100")
        if any(not 0 < s <= 100 for s in self.steps):
            raise ValueError("Canary steps must be within 1..100")

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    def weight_at(self, index: int) -> int:
        return self.steps[min(max(index, 0), self.total_steps - 1)]

    def is_final(self, index: int) -> bool:
        return index >= self.total_steps - 1


@dataclass(frozen=True, slots=True)
class CanaryDecision:
    action: CanaryAction
    reason: str
    next_index: int
    next_weight: int


def decide(
    plan: CanaryPlan,
    *,
    step_index: int,
    seconds_in_step: float,
    assessment: HealthAssessment,
    #: When True, an UNKNOWN verdict that persists past the window aborts rather than
    #: holding forever. This is what prevents a silent candidate from stalling a rollout.
    abort_on_unknown_after_window: bool = True,
) -> CanaryDecision:
    """Decide what to do at the current canary step."""
    current_weight = plan.weight_at(step_index)

    if assessment.verdict is HealthVerdict.UNHEALTHY:
        return CanaryDecision(
            CanaryAction.ABORT, assessment.reason, step_index, current_weight
        )

    window_elapsed = seconds_in_step >= plan.step_seconds

    if assessment.verdict is HealthVerdict.UNKNOWN:
        if window_elapsed and abort_on_unknown_after_window:
            return CanaryDecision(
                CanaryAction.ABORT,
                f"Health could not be confirmed within the observation window: "
                f"{assessment.reason}",
                step_index,
                current_weight,
            )
        return CanaryDecision(
            CanaryAction.HOLD,
            f"Awaiting sufficient telemetry: {assessment.reason}",
            step_index,
            current_weight,
        )

    if not window_elapsed:
        remaining = plan.step_seconds - seconds_in_step
        return CanaryDecision(
            CanaryAction.HOLD,
            f"Healthy at {current_weight}%; {remaining:.0f}s left in the "
            f"observation window.",
            step_index,
            current_weight,
        )

    if plan.is_final(step_index):
        return CanaryDecision(
            CanaryAction.PROMOTE,
            f"Healthy at {current_weight}% for the full observation window; promoting.",
            step_index,
            current_weight,
        )

    next_index = step_index + 1
    next_weight = plan.weight_at(next_index)
    return CanaryDecision(
        CanaryAction.ADVANCE,
        f"Healthy at {current_weight}%; advancing to {next_weight}%.",
        next_index,
        next_weight,
    )


__all__ = ["CanaryAction", "CanaryDecision", "CanaryPlan", "decide"]

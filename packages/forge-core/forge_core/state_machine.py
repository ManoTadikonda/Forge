"""Central state-transition tables.

Every state change in Forge goes through :class:`StateMachine.validate`. Transition
legality is declared in exactly one place so that the API, the worker and the tests
cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Generic, TypeVar

from forge_core.enums import (
    DeploymentState,
    JobState,
    PipelineState,
)
from forge_core.errors import IllegalTransitionError

S = TypeVar("S")


class StateMachine(Generic[S]):
    """A declarative, immutable transition table."""

    def __init__(self, name: str, transitions: Mapping[S, frozenset[S]]) -> None:
        self._name = name
        self._transitions = dict(transitions)

    @property
    def name(self) -> str:
        return self._name

    def allowed_from(self, state: S) -> frozenset[S]:
        return self._transitions.get(state, frozenset())

    def is_terminal(self, state: S) -> bool:
        return not self.allowed_from(state)

    def can(self, source: S, target: S) -> bool:
        return target in self.allowed_from(source)

    def validate(self, source: S, target: S) -> None:
        """Raise :class:`IllegalTransitionError` unless ``source -> target`` is legal."""
        if not self.can(source, target):
            allowed = sorted(str(s) for s in self.allowed_from(source))
            raise IllegalTransitionError(
                f"{self._name}: cannot transition from {source} to {target}",
                details={"from": str(source), "to": str(target), "allowed": allowed},
            )


_P = PipelineState
#: Linear progression with failure/cancellation escapes from every active state.
#: ``QUEUED`` may fail directly (for example when the manifest cannot be loaded).
PIPELINE_MACHINE: StateMachine[PipelineState] = StateMachine(
    "pipeline",
    {
        _P.QUEUED: frozenset({_P.CLONING, _P.FAILED, _P.CANCELED}),
        _P.CLONING: frozenset({_P.VALIDATING, _P.FAILED, _P.CANCELED}),
        _P.VALIDATING: frozenset({_P.TESTING, _P.FAILED, _P.CANCELED}),
        _P.TESTING: frozenset({_P.BUILDING, _P.FAILED, _P.CANCELED}),
        _P.BUILDING: frozenset({_P.PUBLISHING, _P.FAILED, _P.CANCELED}),
        _P.PUBLISHING: frozenset({_P.SUCCEEDED, _P.FAILED, _P.CANCELED}),
        _P.SUCCEEDED: frozenset(),
        _P.FAILED: frozenset(),
        _P.CANCELED: frozenset(),
    },
)

_D = DeploymentState
#: ``ROLLING_BACK`` is reachable from any state in which infrastructure may already
#: have been mutated. It resolves to ROLLED_BACK only after verification succeeds,
#: and to ROLLBACK_FAILED when recovery cannot be confirmed.
DEPLOYMENT_MACHINE: StateMachine[DeploymentState] = StateMachine(
    "deployment",
    {
        _D.PENDING: frozenset({_D.PREPARING, _D.FAILED, _D.CANCELED}),
        _D.PREPARING: frozenset({_D.DEPLOYING, _D.FAILED, _D.CANCELED, _D.ROLLING_BACK}),
        _D.DEPLOYING: frozenset({_D.VERIFYING, _D.FAILED, _D.CANCELED, _D.ROLLING_BACK}),
        _D.VERIFYING: frozenset({_D.CANARY, _D.FAILED, _D.CANCELED, _D.ROLLING_BACK}),
        _D.CANARY: frozenset({_D.PROMOTING, _D.FAILED, _D.CANCELED, _D.ROLLING_BACK}),
        _D.PROMOTING: frozenset({_D.SUCCEEDED, _D.FAILED, _D.ROLLING_BACK}),
        _D.ROLLING_BACK: frozenset({_D.ROLLED_BACK, _D.ROLLBACK_FAILED}),
        _D.SUCCEEDED: frozenset(),
        _D.FAILED: frozenset(),
        _D.CANCELED: frozenset(),
        _D.ROLLED_BACK: frozenset(),
        _D.ROLLBACK_FAILED: frozenset(),
    },
)

_J = JobState
JOB_MACHINE: StateMachine[JobState] = StateMachine(
    "job",
    {
        # A job returns to PENDING when a retry is scheduled or a lease expires.
        _J.PENDING: frozenset({_J.RUNNING, _J.CANCELED, _J.DEAD_LETTER}),
        _J.RUNNING: frozenset(
            {_J.SUCCEEDED, _J.FAILED, _J.PENDING, _J.CANCELED, _J.DEAD_LETTER}
        ),
        _J.FAILED: frozenset({_J.PENDING, _J.DEAD_LETTER}),
        _J.SUCCEEDED: frozenset(),
        _J.CANCELED: frozenset(),
        _J.DEAD_LETTER: frozenset(),
    },
)

__all__ = [
    "DEPLOYMENT_MACHINE",
    "JOB_MACHINE",
    "PIPELINE_MACHINE",
    "StateMachine",
]

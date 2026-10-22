"""State machine transition legality."""

from __future__ import annotations

from itertools import pairwise

import pytest
from forge_core.enums import (
    TERMINAL_DEPLOYMENT_STATES,
    TERMINAL_PIPELINE_STATES,
    DeploymentState,
    JobState,
    PipelineState,
)
from forge_core.errors import IllegalTransitionError
from forge_core.state_machine import (
    DEPLOYMENT_MACHINE,
    JOB_MACHINE,
    PIPELINE_MACHINE,
    StateMachine,
)


class TestPipelineMachine:
    def test_happy_path_is_legal(self) -> None:
        path = [
            PipelineState.QUEUED, PipelineState.CLONING, PipelineState.VALIDATING,
            PipelineState.TESTING, PipelineState.BUILDING, PipelineState.PUBLISHING,
            PipelineState.SUCCEEDED,
        ]
        for source, target in pairwise(path):
            PIPELINE_MACHINE.validate(source, target)

    def test_every_active_state_can_fail(self) -> None:
        for state in PipelineState:
            if state in TERMINAL_PIPELINE_STATES:
                continue
            PIPELINE_MACHINE.validate(state, PipelineState.FAILED)

    def test_every_active_state_can_cancel(self) -> None:
        for state in PipelineState:
            if state in TERMINAL_PIPELINE_STATES:
                continue
            PIPELINE_MACHINE.validate(state, PipelineState.CANCELED)

    def test_cannot_skip_stages(self) -> None:
        with pytest.raises(IllegalTransitionError):
            PIPELINE_MACHINE.validate(PipelineState.QUEUED, PipelineState.SUCCEEDED)
        with pytest.raises(IllegalTransitionError):
            PIPELINE_MACHINE.validate(PipelineState.CLONING, PipelineState.BUILDING)

    def test_cannot_move_backwards(self) -> None:
        with pytest.raises(IllegalTransitionError):
            PIPELINE_MACHINE.validate(PipelineState.BUILDING, PipelineState.CLONING)

    @pytest.mark.parametrize("state", sorted(TERMINAL_PIPELINE_STATES))
    def test_terminal_states_are_final(self, state: PipelineState) -> None:
        assert PIPELINE_MACHINE.is_terminal(state)
        for target in PipelineState:
            with pytest.raises(IllegalTransitionError):
                PIPELINE_MACHINE.validate(state, target)

    def test_error_names_the_allowed_targets(self) -> None:
        with pytest.raises(IllegalTransitionError) as caught:
            PIPELINE_MACHINE.validate(PipelineState.QUEUED, PipelineState.SUCCEEDED)
        details = caught.value.details
        assert details["from"] == "QUEUED"
        assert "CLONING" in details["allowed"]


class TestDeploymentMachine:
    def test_happy_path_is_legal(self) -> None:
        path = [
            DeploymentState.PENDING, DeploymentState.PREPARING,
            DeploymentState.DEPLOYING, DeploymentState.VERIFYING,
            DeploymentState.CANARY, DeploymentState.PROMOTING,
            DeploymentState.SUCCEEDED,
        ]
        for source, target in pairwise(path):
            DEPLOYMENT_MACHINE.validate(source, target)

    @pytest.mark.parametrize(
        "state",
        [
            DeploymentState.PREPARING, DeploymentState.DEPLOYING,
            DeploymentState.VERIFYING, DeploymentState.CANARY,
            DeploymentState.PROMOTING,
        ],
    )
    def test_rollback_reachable_from_any_mutating_state(
        self, state: DeploymentState
    ) -> None:
        """Once infrastructure may have changed, rollback must be reachable."""
        DEPLOYMENT_MACHINE.validate(state, DeploymentState.ROLLING_BACK)

    def test_rollback_resolves_only_to_verified_outcomes(self) -> None:
        allowed = DEPLOYMENT_MACHINE.allowed_from(DeploymentState.ROLLING_BACK)
        assert allowed == frozenset(
            {DeploymentState.ROLLED_BACK, DeploymentState.ROLLBACK_FAILED}
        )

    def test_rolling_back_cannot_become_succeeded(self) -> None:
        """A rolled-back deployment must never be reported as a success."""
        with pytest.raises(IllegalTransitionError):
            DEPLOYMENT_MACHINE.validate(
                DeploymentState.ROLLING_BACK, DeploymentState.SUCCEEDED
            )

    def test_pending_cannot_roll_back(self) -> None:
        """Nothing has been mutated yet, so there is nothing to roll back."""
        with pytest.raises(IllegalTransitionError):
            DEPLOYMENT_MACHINE.validate(
                DeploymentState.PENDING, DeploymentState.ROLLING_BACK
            )

    @pytest.mark.parametrize("state", sorted(TERMINAL_DEPLOYMENT_STATES))
    def test_terminal_states_are_final(self, state: DeploymentState) -> None:
        assert DEPLOYMENT_MACHINE.is_terminal(state)


class TestJobMachine:
    def test_running_can_return_to_pending_for_retry(self) -> None:
        JOB_MACHINE.validate(JobState.RUNNING, JobState.PENDING)

    def test_failed_can_be_retried_or_dead_lettered(self) -> None:
        JOB_MACHINE.validate(JobState.FAILED, JobState.PENDING)
        JOB_MACHINE.validate(JobState.FAILED, JobState.DEAD_LETTER)

    def test_succeeded_is_final(self) -> None:
        assert JOB_MACHINE.is_terminal(JobState.SUCCEEDED)
        with pytest.raises(IllegalTransitionError):
            JOB_MACHINE.validate(JobState.SUCCEEDED, JobState.PENDING)

    def test_dead_letter_is_final(self) -> None:
        assert JOB_MACHINE.is_terminal(JobState.DEAD_LETTER)


class TestStateMachinePrimitive:
    def test_unknown_state_has_no_transitions(self) -> None:
        machine: StateMachine[str] = StateMachine("t", {"a": frozenset({"b"})})
        assert machine.allowed_from("zzz") == frozenset()
        assert machine.is_terminal("zzz")

    def test_can_matches_validate(self) -> None:
        machine: StateMachine[str] = StateMachine("t", {"a": frozenset({"b"})})
        assert machine.can("a", "b")
        assert not machine.can("b", "a")
        machine.validate("a", "b")
        with pytest.raises(IllegalTransitionError):
            machine.validate("b", "a")

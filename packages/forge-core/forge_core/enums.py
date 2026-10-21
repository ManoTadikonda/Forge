"""Enumerations shared across the control plane.

These are persisted by value, so the string values are part of the database
contract and must not be renamed without a migration.
"""

from __future__ import annotations

from enum import StrEnum


class PipelineState(StrEnum):
    QUEUED = "QUEUED"
    CLONING = "CLONING"
    VALIDATING = "VALIDATING"
    TESTING = "TESTING"
    BUILDING = "BUILDING"
    PUBLISHING = "PUBLISHING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class DeploymentState(StrEnum):
    PENDING = "PENDING"
    PREPARING = "PREPARING"
    DEPLOYING = "DEPLOYING"
    VERIFYING = "VERIFYING"
    CANARY = "CANARY"
    PROMOTING = "PROMOTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


class JobState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELED = "CANCELED"


class JobKind(StrEnum):
    RUN_PIPELINE = "RUN_PIPELINE"
    RUN_DEPLOYMENT = "RUN_DEPLOYMENT"


class StageName(StrEnum):
    CLONE = "CLONE"
    VALIDATE = "VALIDATE"
    TEST = "TEST"
    BUILD = "BUILD"
    SBOM = "SBOM"
    SCAN = "SCAN"
    PUBLISH = "PUBLISH"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Role(StrEnum):
    """Roles are ordered by privilege; see `forge_core.authz`."""

    VIEWER = "VIEWER"
    DEVELOPER = "DEVELOPER"
    ADMIN = "ADMIN"


class HealthVerdict(StrEnum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    #: Not enough signal to make a call. Never treated as healthy.
    UNKNOWN = "UNKNOWN"


class ReleaseRole(StrEnum):
    STABLE = "STABLE"
    CANDIDATE = "CANDIDATE"
    RETIRED = "RETIRED"


#: Pipeline states from which no further transition is legal.
TERMINAL_PIPELINE_STATES: frozenset[PipelineState] = frozenset(
    {PipelineState.SUCCEEDED, PipelineState.FAILED, PipelineState.CANCELED}
)

#: Deployment states from which no further transition is legal.
TERMINAL_DEPLOYMENT_STATES: frozenset[DeploymentState] = frozenset(
    {
        DeploymentState.SUCCEEDED,
        DeploymentState.FAILED,
        DeploymentState.CANCELED,
        DeploymentState.ROLLED_BACK,
        DeploymentState.ROLLBACK_FAILED,
    }
)

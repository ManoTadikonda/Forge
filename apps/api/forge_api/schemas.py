"""Request and response models.

ORM objects are never returned directly. Every response goes through an explicit
schema so adding a column — a password hash, an internal token — cannot silently
widen the API surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class PageParams(BaseModel):
    limit: Annotated[int, Field(ge=1, le=200)] = 25
    offset: Annotated[int, Field(ge=0)] = 0


# ------------------------------------------------------------------------- auth


class LoginRequest(BaseModel):
    email: Annotated[str, Field(min_length=3, max_length=320)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - a scheme name, not a credential
    expires_at: datetime
    csrf_token: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


class CurrentUserOut(UserOut):
    permissions: list[str] = Field(default_factory=list)


class CreateUserRequest(BaseModel):
    email: Annotated[str, Field(min_length=3, max_length=320)]
    password: Annotated[str, Field(min_length=12, max_length=1024)]
    display_name: Annotated[str, Field(max_length=120)] = ""
    role: Annotated[str, Field(pattern="^(VIEWER|DEVELOPER|ADMIN)$")] = "VIEWER"


# ------------------------------------------------------------------ repositories


class RepositoryCreate(BaseModel):
    url: Annotated[str, Field(min_length=8, max_length=2048)]
    name: Annotated[str, Field(max_length=128)] = ""
    default_branch: Annotated[str, Field(max_length=255)] = "main"
    description: Annotated[str, Field(max_length=2000)] = ""

    @field_validator("url")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    slug: str
    default_branch: str
    description: str
    is_active: bool
    webhook_enabled: bool
    last_pipeline_at: datetime | None = None
    created_at: datetime


class RepositoryDetail(RepositoryOut):
    pipeline_count: int = 0
    recent_pipelines: list[PipelineOut] = Field(default_factory=list)


# --------------------------------------------------------------------- pipelines


class PipelineCreate(BaseModel):
    repository_id: uuid.UUID
    branch: Annotated[str, Field(max_length=255)] = ""
    #: Repeat requests carrying the same key return the original run rather than
    #: starting a second build.
    idempotency_key: Annotated[str, Field(max_length=128)] = ""


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    sequence: int
    status: str
    exit_code: int | None = None
    message: str
    skip_reason: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


class PipelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID | None = None
    repository_label: str
    run_number: int
    state: str
    branch: str
    commit_sha: str
    commit_message: str
    commit_author: str
    trigger: str
    failure_reason: str
    cancel_requested: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime


class PipelineDetail(PipelineOut):
    stages: list[StageOut] = Field(default_factory=list)
    artifacts: list[ArtifactOut] = Field(default_factory=list)
    manifest: dict[str, Any] | None = None


class LogLine(BaseModel):
    sequence: int
    stage: str
    content: str
    truncated: bool
    created_at: datetime


# --------------------------------------------------------------------- artifacts


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    digest: str
    image_name: str
    version: str
    registry_name: str
    reference: str
    size_bytes: int
    pipeline_run_id: uuid.UUID | None = None
    repository_url: str
    commit_sha: str
    builder: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    sbom_format: str
    sbom_component_count: int
    signed: bool
    signature_reference: str
    scan_status: str
    scan_skip_reason: str
    created_at: datetime


# ------------------------------------------------------------------- deployments


class DeploymentCreate(BaseModel):
    service_id: uuid.UUID | None = None
    service_name: Annotated[str, Field(max_length=64)] = ""
    environment: Annotated[str, Field(max_length=64)] = "development"
    artifact_id: uuid.UUID | None = None
    artifact_digest: Annotated[str, Field(max_length=80)] = ""
    idempotency_key: Annotated[str, Field(max_length=128)] = ""
    #: Per-deployment rollout overrides, validated against the canary schema.
    canary_steps: list[Annotated[int, Field(ge=1, le=100)]] | None = None
    step_seconds: Annotated[int, Field(ge=1, le=3600)] | None = None
    #: Environment passed to the candidate release. Used by the demo to configure
    #: the deliberately faulty version.
    release_env: dict[str, str] = Field(default_factory=dict)


class DeploymentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    kind: str
    from_state: str
    to_state: str
    message: str
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class HealthSampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_role: str
    version: str
    observed_at: datetime
    requests: int
    errors: int
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    healthy_endpoint: bool
    ready_endpoint: bool


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_id: uuid.UUID
    environment_id: uuid.UUID
    artifact_id: uuid.UUID
    previous_artifact_id: uuid.UUID | None = None
    state: str
    deployment_number: int
    strategy: str
    current_step_index: int
    current_weight: int
    failure_reason: str
    rollback_reason: str
    rollback_verified: bool
    health_summary: dict[str, Any] = Field(default_factory=dict)
    cancel_requested: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime


class DeploymentDetail(DeploymentOut):
    service_name: str = ""
    environment_name: str = ""
    version: str = ""
    artifact_digest: str = ""
    previous_version: str = ""
    events: list[DeploymentEventOut] = Field(default_factory=list)
    health_samples: list[HealthSampleOut] = Field(default_factory=list)
    canary_plan: list[int] = Field(default_factory=list)


# ------------------------------------------------------- environments & services


class EnvironmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    display_name: str
    is_protected: bool
    created_at: datetime


class ReleaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: str
    role: str
    weight: int
    is_running: bool
    endpoints: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    stopped_at: datetime | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    environment_id: uuid.UUID
    environment_name: str = ""
    repository_id: uuid.UUID | None = None
    current_artifact_id: uuid.UUID | None = None
    current_version: str = ""
    created_at: datetime


class ServiceHealthOut(BaseModel):
    service: ServiceOut
    releases: list[ReleaseOut] = Field(default_factory=list)
    router_weights: dict[str, int] = Field(default_factory=dict)
    router_reachable: bool = False
    last_deployment_state: str = ""


# ----------------------------------------------------------- incidents and audit


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    deployment_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    kind: str
    severity: str
    title: str
    detail: str
    verdict: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    resolved_at: datetime | None = None
    created_at: datetime


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_label: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    ip_address: str
    request_id: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ---------------------------------------------------------------------- overview


class OverviewStats(BaseModel):
    repositories: int = 0
    pipelines_total: int = 0
    pipelines_succeeded: int = 0
    pipelines_failed: int = 0
    pipelines_running: int = 0
    pipeline_success_rate: float | None = None
    deployments_total: int = 0
    deployments_succeeded: int = 0
    deployments_rolled_back: int = 0
    deployments_active: int = 0
    artifacts: int = 0
    open_incidents: int = 0
    queue_depth: dict[str, int] = Field(default_factory=dict)


class OverviewOut(BaseModel):
    stats: OverviewStats
    recent_pipelines: list[PipelineOut] = Field(default_factory=list)
    recent_deployments: list[DeploymentOut] = Field(default_factory=list)
    recent_incidents: list[IncidentOut] = Field(default_factory=list)


class SettingsOut(BaseModel):
    """Operational configuration exposed to the dashboard.

    Contains no secrets: only whether each one is configured.
    """

    environment: str
    version: str
    builder: str
    registry: str
    deploy_adapter: str
    canary_steps: list[int]
    canary_step_seconds: int
    health_min_samples: int
    health_max_error_rate: float
    health_max_p95_latency_ms: float
    rate_limit_enabled: bool
    webhook_secret_configured: bool
    redis_configured: bool
    rate_limiter_backend: str
    router_admin_url: str
    unsandboxed_builds_allowed: bool


Page.model_rebuild()
RepositoryDetail.model_rebuild()
PipelineDetail.model_rebuild()


__all__ = [
    "ArtifactOut", "AuditEventOut", "CreateUserRequest", "CurrentUserOut",
    "DeploymentCreate", "DeploymentDetail", "DeploymentEventOut", "DeploymentOut",
    "EnvironmentOut", "HealthSampleOut", "IncidentOut", "LogLine", "LoginRequest",
    "OverviewOut", "OverviewStats", "Page", "PageParams", "PipelineCreate",
    "PipelineDetail", "PipelineOut", "ReleaseOut", "RepositoryCreate",
    "RepositoryDetail", "RepositoryOut", "ServiceHealthOut", "ServiceOut",
    "SettingsOut", "StageOut", "TokenResponse", "UserOut",
]

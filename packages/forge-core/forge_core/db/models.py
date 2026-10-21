"""Relational model.

Schema conventions:

* UUID primary keys everywhere, so identifiers can be minted client-side and are not
  guessable or enumerable.
* Enum-valued columns are stored as short ``String`` with a CHECK constraint rather
  than native PostgreSQL ENUM types: adding a value then needs no ``ALTER TYPE``,
  which keeps migrations non-blocking.
* Large or append-heavy payloads (build logs) live in ``log_chunks``, never inline in
  a frequently-updated row, so pipeline rows stay narrow and cheap to update.
* Historical records (pipelines, deployments, audit) survive deletion of the parent
  repository via ``ON DELETE SET NULL`` plus a denormalised ``repository_label``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from forge_core.db.base import Base, TimestampMixin, uuid_pk
from forge_core.enums import (
    DeploymentState,
    HealthVerdict,
    JobKind,
    JobState,
    PipelineState,
    ReleaseRole,
    Role,
    StageName,
    StageStatus,
)


def _check_in(column: str, values: type, name: str) -> CheckConstraint:
    allowed = ", ".join(f"'{v.value}'" for v in values)  # type: ignore[attr-defined]
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


# --------------------------------------------------------------------------- identity


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=Role.VIEWER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    #: Emails are normalised to lowercase before persistence (see ``normalize_email``),
    #: so a plain unique constraint gives case-insensitive uniqueness without needing a
    #: functional index.
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        _check_in("role", Role, "role_valid"),
    )


class Session(Base, TimestampMixin):
    """Server-side session records, so a token can be revoked before it expires."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Matches the JWT ``jti``.
    jti: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: CSRF token bound to this session, for cookie-authenticated browser clients.
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (
        UniqueConstraint("jti", name="uq_sessions_jti"),
        Index("ix_sessions_user_expires", "user_id", "expires_at"),
    )


# ------------------------------------------------------------------------ repositories


class Repository(Base, TimestampMixin):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    #: Normalised ``host/owner/name`` used for uniqueness and display.
    slug: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    last_pipeline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pipelines: Mapped[list[PipelineRun]] = relationship(back_populates="repository")

    __table_args__ = (
        UniqueConstraint("slug", name="uq_repositories_slug"),
        Index("ix_repositories_active", "is_active"),
    )


class Environment(Base, TimestampMixin):
    __tablename__ = "environments"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    #: Production-like environments require stricter gates.
    is_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Per-environment rollout overrides (canary steps, thresholds).
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (UniqueConstraint("name", name="uq_environments_name"),)


class Service(Base, TimestampMixin):
    """A deployable unit: one repository's application in one environment."""

    __tablename__ = "services"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL")
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Digest of the release currently serving stable traffic.
    current_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    environment: Mapped[Environment] = relationship()

    __table_args__ = (
        UniqueConstraint("name", "environment_id", name="uq_services_name_environment"),
    )


# ---------------------------------------------------------------------------- pipeline


class PipelineRun(Base, TimestampMixin):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), index=True
    )
    #: Retained so history remains readable after the repository is removed.
    repository_label: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    #: Monotonic per-repository counter for human-friendly references (#1, #2, ...).
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PipelineState.QUEUED.value, index=True
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    commit_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    commit_author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Caller-supplied idempotency key; a repeat request returns the original run.
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    repository: Mapped[Repository | None] = relationship(back_populates="pipelines")
    stages: Mapped[list[PipelineStage]] = relationship(
        back_populates="run", cascade="all, delete-orphan",
        order_by="PipelineStage.sequence",
    )
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="pipeline_run")

    __table_args__ = (
        _check_in("state", PipelineState, "state_valid"),
        UniqueConstraint("repository_id", "run_number", name="uq_pipeline_runs_number"),
        UniqueConstraint(
            "repository_id", "idempotency_key", name="uq_pipeline_runs_idempotency"
        ),
        Index("ix_pipeline_runs_repo_created", "repository_id", "created_at"),
        Index("ix_pipeline_runs_state_created", "state", "created_at"),
    )


class PipelineStage(Base, TimestampMixin):
    """One stage of a run. Stage rows are the durable timeline shown in the UI."""

    __tablename__ = "pipeline_stages"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=StageStatus.PENDING.value
    )
    exit_code: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Why an optional gate was skipped (e.g. "scanner not installed").
    skip_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    run: Mapped[PipelineRun] = relationship(back_populates="stages")

    __table_args__ = (
        _check_in("name", StageName, "name_valid"),
        _check_in("status", StageStatus, "status_valid"),
        UniqueConstraint("run_id", "sequence", name="uq_pipeline_stages_sequence"),
    )


class LogChunk(Base):
    """Append-only log storage, keyed by owner.

    Logs are chunked so a long build does not rewrite one ever-growing row, and so the
    API can paginate output without loading an entire build's output into memory.
    """

    __tablename__ = "log_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: "pipeline" | "deployment"
    owner_type: Mapped[str] = mapped_column(String(24), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: True when output was cut off by the configured log size limit.
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "sequence", name="uq_log_chunks_seq"),
        Index("ix_log_chunks_owner", "owner_type", "owner_id", "sequence"),
        CheckConstraint(
            "owner_type IN ('pipeline', 'deployment')", name="ck_log_chunks_owner_valid"
        ),
    )


# --------------------------------------------------------------------------- artifacts


class Artifact(Base, TimestampMixin):
    """An immutable build output, addressed by digest."""

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: ``sha256:<hex>``. Globally unique: the same content is the same artifact.
    digest: Mapped[str] = mapped_column(String(80), nullable=False)
    image_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    registry: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    reference: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # provenance
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    repository_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    builder: Mapped[str] = mapped_column(String(32), nullable=False, default="process")
    build_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    build_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # supply chain
    sbom_format: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    sbom_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    sbom_component_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Only true when a real signature was produced; never set optimistically.
    signed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signature_reference: Mapped[str] = mapped_column(
        String(512), nullable=False, default=""
    )
    scan_status: Mapped[str] = mapped_column(String(24), nullable=False, default="SKIPPED")
    scan_skip_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scan_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    pipeline_run: Mapped[PipelineRun | None] = relationship(back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint("digest", name="uq_artifacts_digest"),
        Index("ix_artifacts_image_version", "image_name", "version"),
    )


# ------------------------------------------------------------------------- deployments


class Deployment(Base, TimestampMixin):
    __tablename__ = "deployments"

    id: Mapped[uuid.UUID] = uuid_pk()
    service_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    #: The artifact serving before this deployment; the rollback target.
    previous_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=DeploymentState.PENDING.value, index=True
    )
    deployment_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    strategy: Mapped[str] = mapped_column(String(24), nullable=False, default="canary")
    canary_steps: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    current_step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    failure_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rollback_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Only set True after post-rollback HTTP verification succeeds.
    rollback_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    health_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    service: Mapped[Service] = relationship()
    artifact: Mapped[Artifact] = relationship(foreign_keys=[artifact_id])
    previous_artifact: Mapped[Artifact | None] = relationship(
        foreign_keys=[previous_artifact_id]
    )
    events: Mapped[list[DeploymentEvent]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan",
        order_by="DeploymentEvent.sequence",
    )

    __table_args__ = (
        _check_in("state", DeploymentState, "state_valid"),
        UniqueConstraint(
            "service_id", "idempotency_key", name="uq_deployments_idempotency"
        ),
        UniqueConstraint(
            "service_id", "deployment_number", name="uq_deployments_number"
        ),
        CheckConstraint(
            "current_weight >= 0 AND current_weight <= 100", name="ck_deployments_weight"
        ),
        Index("ix_deployments_service_created", "service_id", "created_at"),
    )


class DeploymentEvent(Base):
    """Durable, ordered timeline. Every state transition writes one row."""

    __tablename__ = "deployment_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    from_state: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    to_state: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: User id, or the worker identity that performed the transition.
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    deployment: Mapped[Deployment] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint("deployment_id", "sequence", name="uq_deployment_events_seq"),
    )


class Release(Base, TimestampMixin):
    """A running instance set for one artifact, as known to the deployment adapter."""

    __tablename__ = "releases"

    id: Mapped[uuid.UUID] = uuid_pk()
    service_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="SET NULL")
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ReleaseRole.CANDIDATE.value
    )
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    endpoints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    instances: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_running: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _check_in("role", ReleaseRole, "role_valid"),
        Index("ix_releases_service_running", "service_id", "is_running"),
    )


class HealthSampleRow(Base):
    """One scraped observation. Written by the controller during a rollout."""

    __tablename__ = "health_samples"

    id: Mapped[uuid.UUID] = uuid_pk()
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("releases.id", ondelete="SET NULL")
    )
    #: STABLE or CANDIDATE at the time of measurement.
    release_role: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_p50_ms: Mapped[float | None] = mapped_column(Float)
    latency_p95_ms: Mapped[float | None] = mapped_column(Float)
    latency_p99_ms: Mapped[float | None] = mapped_column(Float)
    healthy_endpoint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ready_endpoint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_health_samples_deploy_observed", "deployment_id", "observed_at"),
        Index("ix_health_samples_service_observed", "service_id", "observed_at"),
    )


class Incident(Base, TimestampMixin):
    """A rollback or failed-release record; the observable alert required by the spec."""

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = uuid_pk()
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="SET NULL"), index=True
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("services.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False, default="ROLLBACK")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="HIGH")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verdict: Mapped[str] = mapped_column(
        String(16), nullable=False, default=HealthVerdict.UNHEALTHY.value
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_incidents_created", "created_at"),)


# ------------------------------------------------------------------------------- queue


class Job(Base, TimestampMixin):
    """Durable work item.

    Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` over
    ``state='PENDING' AND available_at <= now()``. ``lease_expires_at`` plus
    ``fence_token`` make an expired worker's writes detectable and rejectable.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JobState.PENDING.value
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Unique per logical unit of work; makes enqueue idempotent under retry.
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    worker_id: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Incremented on every claim. A worker whose token is stale must not write.
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _check_in("state", JobState, "state_valid"),
        _check_in("kind", JobKind, "kind_valid"),
        UniqueConstraint("dedupe_key", name="uq_jobs_dedupe_key"),
        # The hot path for claiming: narrow partial index over runnable work.
        Index(
            "ix_jobs_runnable",
            "priority", "available_at",
            postgresql_where=(state == JobState.PENDING.value),
        ),
        Index("ix_jobs_lease", "state", "lease_expires_at"),
    )


class DeploymentLock(Base):
    """Mutual exclusion for deployments targeting one service+environment.

    Kept in PostgreSQL alongside the state it protects: a lock that can disagree with
    the state it guards is not a lock. ``token`` increments on every acquisition so a
    previous holder whose lease expired is fenced out of all writes.
    """

    __tablename__ = "deployment_locks"

    resource: Mapped[str] = mapped_column(String(200), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class WebhookDelivery(Base):
    """Received webhook deliveries, for deduplication and audit."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: GitHub's ``X-GitHub-Delivery`` UUID; the natural dedup key.
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL")
    )
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: True when this delivery was a repeat and was intentionally ignored.
    duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
    result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_digest: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint("delivery_id", name="uq_webhook_deliveries_delivery_id"),
    )


class AuditEvent(Base):
    """Append-only record of security-sensitive operations."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_label: Mapped[str] = mapped_column(String(320), nullable=False, default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="SUCCESS")
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_audit_events_actor_created", "actor_id", "created_at"),
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
        CheckConstraint(
            "outcome IN ('SUCCESS', 'FAILURE', 'DENIED')", name="ck_audit_events_outcome"
        ),
    )


def normalize_email(email: str) -> str:
    """Canonical form used for storage and lookup."""
    return email.strip().lower()


__all__ = [
    "Artifact",
    "AuditEvent",
    "Deployment",
    "DeploymentEvent",
    "DeploymentLock",
    "Environment",
    "HealthSampleRow",
    "Incident",
    "Job",
    "LogChunk",
    "PipelineRun",
    "PipelineStage",
    "Release",
    "Repository",
    "Service",
    "Session",
    "User",
    "WebhookDelivery",
    "normalize_email",
]

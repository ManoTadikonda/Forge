"""Deployment creation, inspection, cancellation and rollback."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from forge_core.authz import Permission
from forge_core.db import queue
from forge_core.db.events import record_audit, record_event
from forge_core.db.models import (
    Artifact,
    Deployment,
    DeploymentEvent,
    Environment,
    HealthSampleRow,
    Service,
)
from forge_core.enums import DeploymentState, JobKind
from forge_core.errors import ConflictError, NotFoundError, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from forge_api.deps import (
    CurrentUserDep,
    DbSession,
    commit,
    enforce_rate_limit,
    require,
)
from forge_api.schemas import (
    DeploymentCreate,
    DeploymentDetail,
    DeploymentEventOut,
    DeploymentOut,
    HealthSampleOut,
    Page,
)

router = APIRouter(prefix="/deployments", tags=["deployments"])

ACTIVE_STATES = [
    DeploymentState.PENDING.value,
    DeploymentState.PREPARING.value,
    DeploymentState.DEPLOYING.value,
    DeploymentState.VERIFYING.value,
    DeploymentState.CANARY.value,
    DeploymentState.PROMOTING.value,
    DeploymentState.ROLLING_BACK.value,
]


def job_key(deployment_id: uuid.UUID) -> str:
    return f"deployment:{deployment_id}"


@router.post(
    "",
    response_model=DeploymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a deployment",
)
async def create_deployment(
    payload: DeploymentCreate,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_CREATE))],
) -> DeploymentOut:
    settings = request.app.state.settings
    await enforce_rate_limit(
        request, "deployment_create", limit=settings.rate_limit_write_per_minute
    )

    artifact = await _resolve_artifact(session, payload)
    environment = (
        await session.execute(select(Environment).where(Environment.name == payload.environment))
    ).scalar_one_or_none()
    if environment is None:
        raise NotFoundError(f"No such environment: {payload.environment!r}.")

    service = await _resolve_service(session, payload, artifact, environment)

    if payload.idempotency_key:
        existing = (
            await session.execute(
                select(Deployment).where(
                    Deployment.service_id == service.id,
                    Deployment.idempotency_key == payload.idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return DeploymentOut.model_validate(existing)

    # Reject rather than queue: two concurrent rollouts of the same service would
    # fight over traffic weights. The worker's lock is the second line of defence.
    active = int(
        await session.scalar(
            select(func.count(Deployment.id)).where(
                Deployment.service_id == service.id,
                Deployment.state.in_(ACTIVE_STATES),
            )
        )
        or 0
    )
    if active:
        raise ConflictError(
            "A deployment is already in progress for this service and environment.",
            details={"service": service.name, "environment": environment.name},
        )

    steps = payload.canary_steps or list(settings.canary_steps)
    if steps != sorted(steps) or steps[-1] != 100:
        raise ValidationError("canary_steps must be ascending and finish at 100.")

    next_number = int(
        await session.scalar(
            select(func.coalesce(func.max(Deployment.deployment_number), 0) + 1).where(
                Deployment.service_id == service.id
            )
        )
        or 1
    )

    deployment = Deployment(
        id=uuid.uuid4(),
        service_id=service.id,
        environment_id=environment.id,
        artifact_id=artifact.id,
        previous_artifact_id=service.current_artifact_id,
        state=DeploymentState.PENDING.value,
        deployment_number=next_number,
        canary_steps={
            "steps": steps,
            "step_seconds": payload.step_seconds or settings.canary_step_seconds,
            "release_env": dict(payload.release_env),
            "min_samples": settings.health_min_samples,
            "max_error_rate": settings.health_max_error_rate,
            "max_p95_latency_ms": settings.health_max_p95_latency_ms,
            "router_port": 8098,
        },
        triggered_by_id=user.id,
        idempotency_key=payload.idempotency_key or None,
    )
    session.add(deployment)
    await session.flush()

    await record_event(
        session,
        deployment.id,
        kind="created",
        message=f"Deployment of {artifact.image_name} {artifact.version} "
        f"({artifact.digest[:19]}…) to {environment.name} requested.",
        to_state=DeploymentState.PENDING.value,
        actor=user.email,
        payload={"digest": artifact.digest, "steps": steps},
    )
    # Enqueued in the same transaction as the row it refers to.
    await queue.enqueue(
        session,
        kind=JobKind.RUN_DEPLOYMENT,
        dedupe_key=job_key(deployment.id),
        payload={"deployment_id": str(deployment.id)},
        max_attempts=1,
    )
    await record_audit(
        session,
        action="deployment.create",
        actor_id=user.id,
        actor_label=user.email,
        resource_type="deployment",
        resource_id=str(deployment.id),
        detail={
            "service": service.name,
            "environment": environment.name,
            "digest": artifact.digest,
        },
        ip_address=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    # The worker claims this deployment from the queue and the caller polls it;
    # both need the row to be durable before the response is returned.
    await commit(session)
    return DeploymentOut.model_validate(deployment)


async def _resolve_artifact(session: AsyncSession, payload: DeploymentCreate) -> Artifact:
    if payload.artifact_id is not None:
        artifact = await session.get(Artifact, payload.artifact_id)
    elif payload.artifact_digest:
        artifact = (
            await session.execute(
                select(Artifact).where(Artifact.digest == payload.artifact_digest)
            )
        ).scalar_one_or_none()
    else:
        raise ValidationError("Provide either artifact_id or artifact_digest.")
    if artifact is None:
        raise NotFoundError("No such artifact.")
    return artifact


async def _resolve_service(
    session: AsyncSession,
    payload: DeploymentCreate,
    artifact: Artifact,
    environment: Environment,
) -> Service:
    if payload.service_id is not None:
        service = await session.get(Service, payload.service_id)
        if service is None:
            raise NotFoundError("No such service.")
        return service

    name = payload.service_name or artifact.image_name
    service = (
        await session.execute(
            select(Service).where(Service.name == name, Service.environment_id == environment.id)
        )
    ).scalar_one_or_none()
    if service is None:
        # First deployment of this service into this environment creates it.
        service = Service(id=uuid.uuid4(), name=name, environment_id=environment.id, config={})
        session.add(service)
        await session.flush()
    return service


@router.get("", response_model=Page[DeploymentOut], summary="List deployments")
async def list_deployments(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_READ))],
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    service_id: uuid.UUID | None = None,
    state: Annotated[str, Query(max_length=24)] = "",
) -> Page[DeploymentOut]:
    conditions: list[Any] = []
    if service_id is not None:
        conditions.append(Deployment.service_id == service_id)
    if state:
        if state not in {s.value for s in DeploymentState}:
            raise ValidationError(f"Unknown deployment state {state!r}.")
        conditions.append(Deployment.state == state)

    total = int(await session.scalar(select(func.count(Deployment.id)).where(*conditions)) or 0)
    rows = (
        await session.execute(
            select(Deployment)
            .where(*conditions)
            .order_by(Deployment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return Page(
        items=[DeploymentOut.model_validate(d) for d in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{deployment_id}", response_model=DeploymentDetail, summary="Inspect")
async def get_deployment(
    deployment_id: uuid.UUID,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_READ))],
) -> DeploymentDetail:
    deployment = (
        await session.execute(
            select(Deployment)
            .where(Deployment.id == deployment_id)
            .options(selectinload(Deployment.events))
        )
    ).scalar_one_or_none()
    if deployment is None:
        raise NotFoundError("No such deployment.")

    service = await session.get(Service, deployment.service_id)
    environment = await session.get(Environment, deployment.environment_id)
    artifact = await session.get(Artifact, deployment.artifact_id)
    previous = (
        await session.get(Artifact, deployment.previous_artifact_id)
        if deployment.previous_artifact_id
        else None
    )
    samples = (
        await session.execute(
            select(HealthSampleRow)
            .where(HealthSampleRow.deployment_id == deployment_id)
            .order_by(HealthSampleRow.observed_at)
            .limit(500)
        )
    ).scalars()

    detail = DeploymentDetail.model_validate(deployment)
    detail.service_name = service.name if service else ""
    detail.environment_name = environment.name if environment else ""
    detail.version = artifact.version if artifact else ""
    detail.artifact_digest = artifact.digest if artifact else ""
    detail.previous_version = previous.version if previous else ""
    detail.canary_plan = list((deployment.canary_steps or {}).get("steps", []))
    detail.events = [
        DeploymentEventOut.model_validate(e)
        for e in sorted(deployment.events, key=lambda e: e.sequence)
    ]
    detail.health_samples = [HealthSampleOut.model_validate(s) for s in samples]
    return detail


@router.get(
    "/{deployment_id}/events",
    response_model=list[DeploymentEventOut],
    summary="Deployment timeline",
)
async def get_events(
    deployment_id: uuid.UUID,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_READ))],
    after: Annotated[int, Query(ge=-1)] = -1,
) -> list[DeploymentEventOut]:
    """Events after ``after``. The dashboard polls this for live updates."""
    rows = (
        await session.execute(
            select(DeploymentEvent)
            .where(
                DeploymentEvent.deployment_id == deployment_id,
                DeploymentEvent.sequence > after,
            )
            .order_by(DeploymentEvent.sequence)
            .limit(500)
        )
    ).scalars()
    return [DeploymentEventOut.model_validate(e) for e in rows]


@router.post("/{deployment_id}/cancel", response_model=DeploymentOut, summary="Cancel")
async def cancel_deployment(
    deployment_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_CANCEL))],
) -> DeploymentOut:
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None:
        raise NotFoundError("No such deployment.")

    if DeploymentState(deployment.state) not in {DeploymentState(s) for s in ACTIVE_STATES}:
        # Idempotent: cancelling a finished deployment is accepted and is a no-op.
        return DeploymentOut.model_validate(deployment)

    deployment.cancel_requested = True
    await queue.request_cancel(session, job_key(deployment_id))
    await record_event(
        session,
        deployment_id,
        kind="cancel_requested",
        message="Cancellation requested by an operator.",
        actor=user.email,
    )
    await record_audit(
        session,
        action="deployment.cancel",
        actor_id=user.id,
        actor_label=user.email,
        resource_type="deployment",
        resource_id=str(deployment_id),
        ip_address=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    await commit(session)
    return DeploymentOut.model_validate(deployment)


@router.post(
    "/{deployment_id}/rollback",
    response_model=DeploymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Roll back to the previous artifact",
)
async def rollback_deployment(
    deployment_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_ROLLBACK))],
) -> DeploymentOut:
    """Manually roll back by deploying the previous artifact.

    Expressed as a new deployment rather than a mutation of the old one, so the
    rollout goes through the same verified path as any other release and the history
    records both events.
    """
    original = await session.get(Deployment, deployment_id)
    if original is None:
        raise NotFoundError("No such deployment.")
    if original.previous_artifact_id is None:
        raise ConflictError("This deployment has no previous artifact to roll back to.")
    if original.state in ACTIVE_STATES:
        raise ConflictError("This deployment is still in progress; cancel it before rolling back.")

    target = await session.get(Artifact, original.previous_artifact_id)
    if target is None:
        raise NotFoundError("The previous artifact is no longer available.")

    service = await session.get(Service, original.service_id)
    environment = await session.get(Environment, original.environment_id)
    if service is None or environment is None:  # pragma: no cover - FK protected
        raise NotFoundError("The deployment's service or environment is missing.")

    active = int(
        await session.scalar(
            select(func.count(Deployment.id)).where(
                Deployment.service_id == service.id,
                Deployment.state.in_(ACTIVE_STATES),
            )
        )
        or 0
    )
    if active:
        raise ConflictError("A deployment is already in progress for this service.")

    next_number = int(
        await session.scalar(
            select(func.coalesce(func.max(Deployment.deployment_number), 0) + 1).where(
                Deployment.service_id == service.id
            )
        )
        or 1
    )
    settings = request.app.state.settings
    deployment = Deployment(
        id=uuid.uuid4(),
        service_id=service.id,
        environment_id=environment.id,
        artifact_id=target.id,
        previous_artifact_id=original.artifact_id,
        state=DeploymentState.PENDING.value,
        deployment_number=next_number,
        canary_steps={
            # A manual rollback goes straight to 100%: the target version is already
            # known good, and a slow canary prolongs the incident.
            "steps": [100],
            "step_seconds": 5,
            "release_env": {},
            "min_samples": settings.health_min_samples,
            "max_error_rate": settings.health_max_error_rate,
            "max_p95_latency_ms": settings.health_max_p95_latency_ms,
            "router_port": 8098,
        },
        triggered_by_id=user.id,
    )
    session.add(deployment)
    await session.flush()
    await record_event(
        session,
        deployment.id,
        kind="manual_rollback",
        message=f"Manual rollback to {target.version} ({target.digest[:19]}…) "
        f"requested by {user.email}.",
        to_state=DeploymentState.PENDING.value,
        actor=user.email,
        payload={"rolled_back_from": str(deployment_id)},
    )
    await queue.enqueue(
        session,
        kind=JobKind.RUN_DEPLOYMENT,
        dedupe_key=job_key(deployment.id),
        payload={"deployment_id": str(deployment.id)},
        max_attempts=1,
    )
    await record_audit(
        session,
        action="deployment.rollback",
        actor_id=user.id,
        actor_label=user.email,
        resource_type="deployment",
        resource_id=str(deployment.id),
        detail={"from_deployment": str(deployment_id), "to_digest": target.digest},
        ip_address=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    await commit(session)
    return DeploymentOut.model_validate(deployment)

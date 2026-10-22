"""Environments, services and live service health."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Query
from forge_core.authz import Permission
from forge_core.db import queue
from forge_core.db.models import (
    Artifact,
    Deployment,
    Environment,
    Incident,
    PipelineRun,
    Release,
    Repository,
    Service,
)
from forge_core.enums import DeploymentState, PipelineState
from forge_core.errors import NotFoundError
from sqlalchemy import func, select

from forge_api.deps import CurrentUserDep, DbSession, require
from forge_api.schemas import (
    DeploymentOut,
    EnvironmentOut,
    IncidentOut,
    OverviewOut,
    OverviewStats,
    PipelineOut,
    ReleaseOut,
    ServiceHealthOut,
    ServiceOut,
)

router = APIRouter(tags=["environments"])


@router.get("/environments", response_model=list[EnvironmentOut], summary="List")
async def list_environments(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_READ))],
) -> list[EnvironmentOut]:
    rows = (await session.execute(select(Environment).order_by(Environment.name))).scalars()
    return [EnvironmentOut.model_validate(e) for e in rows]


@router.get("/services", response_model=list[ServiceOut], summary="List services")
async def list_services(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_READ))],
    environment: Annotated[str, Query(max_length=64)] = "",
) -> list[ServiceOut]:
    statement = select(Service, Environment).join(
        Environment, Service.environment_id == Environment.id
    )
    if environment:
        statement = statement.where(Environment.name == environment)

    results = []
    for service, env in (await session.execute(statement.order_by(Service.name))).all():
        out = ServiceOut.model_validate(service)
        out.environment_name = env.name
        if service.current_artifact_id:
            artifact = await session.get(Artifact, service.current_artifact_id)
            out.current_version = artifact.version if artifact else ""
        results.append(out)
    return results


@router.get(
    "/services/{service_id}/health",
    response_model=ServiceHealthOut,
    summary="Live service health",
)
async def service_health(
    service_id: uuid.UUID,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_READ))],
) -> ServiceHealthOut:
    service = await session.get(Service, service_id)
    if service is None:
        raise NotFoundError("No such service.")
    environment = await session.get(Environment, service.environment_id)

    releases = (
        await session.execute(
            select(Release)
            .where(Release.service_id == service_id)
            .order_by(Release.created_at.desc())
            .limit(20)
        )
    ).scalars()

    out = ServiceOut.model_validate(service)
    out.environment_name = environment.name if environment else ""
    if service.current_artifact_id:
        artifact = await session.get(Artifact, service.current_artifact_id)
        out.current_version = artifact.version if artifact else ""

    # Live routing weights come from the router itself, not from our own records:
    # reporting our intent as if it were reality would hide a drift bug.
    weights: dict[str, int] = {}
    reachable = False
    last_state = ""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                "http://127.0.0.1:8099/stats",
                params={
                    "service": service.name,
                    "environment": environment.name if environment else "",
                },
            )
            if response.status_code == 200:
                weights = dict(response.json().get("weights", {}))
                reachable = True
            elif response.status_code == 404:
                reachable = True  # router is up, this service simply has no route
    except httpx.RequestError:
        reachable = False

    latest = (
        await session.execute(
            select(Deployment)
            .where(Deployment.service_id == service_id)
            .order_by(Deployment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is not None:
        last_state = latest.state

    return ServiceHealthOut(
        service=out,
        releases=[ReleaseOut.model_validate(r) for r in releases],
        router_weights=weights,
        router_reachable=reachable,
        last_deployment_state=last_state,
    )


@router.get("/overview", response_model=OverviewOut, summary="Dashboard overview")
async def overview(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.PIPELINE_READ))],
) -> OverviewOut:
    """Aggregate counts for the dashboard.

    Every number is computed from the database. When nothing has run, the response is
    genuinely empty and the UI renders an empty state rather than invented history.
    """

    async def count(model: Any, *conditions: Any) -> int:
        return int(await session.scalar(select(func.count(model.id)).where(*conditions)) or 0)

    active_pipeline_states = [
        PipelineState.QUEUED.value,
        PipelineState.CLONING.value,
        PipelineState.VALIDATING.value,
        PipelineState.TESTING.value,
        PipelineState.BUILDING.value,
        PipelineState.PUBLISHING.value,
    ]
    active_deployment_states = [
        DeploymentState.PENDING.value,
        DeploymentState.PREPARING.value,
        DeploymentState.DEPLOYING.value,
        DeploymentState.VERIFYING.value,
        DeploymentState.CANARY.value,
        DeploymentState.PROMOTING.value,
        DeploymentState.ROLLING_BACK.value,
    ]

    succeeded = await count(PipelineRun, PipelineRun.state == PipelineState.SUCCEEDED.value)
    failed = await count(PipelineRun, PipelineRun.state == PipelineState.FAILED.value)
    finished = succeeded + failed

    stats = OverviewStats(
        repositories=await count(Repository, Repository.is_active.is_(True)),
        pipelines_total=await count(PipelineRun),
        pipelines_succeeded=succeeded,
        pipelines_failed=failed,
        pipelines_running=await count(PipelineRun, PipelineRun.state.in_(active_pipeline_states)),
        # None, not 0, when nothing has finished: "no data" and "0% success" are
        # different statements and the UI must not conflate them.
        pipeline_success_rate=(succeeded / finished) if finished else None,
        deployments_total=await count(Deployment),
        deployments_succeeded=await count(
            Deployment, Deployment.state == DeploymentState.SUCCEEDED.value
        ),
        deployments_rolled_back=await count(
            Deployment,
            Deployment.state.in_(
                [
                    DeploymentState.ROLLED_BACK.value,
                    DeploymentState.ROLLBACK_FAILED.value,
                ]
            ),
        ),
        deployments_active=await count(Deployment, Deployment.state.in_(active_deployment_states)),
        artifacts=await count(Artifact),
        open_incidents=await count(Incident, Incident.resolved_at.is_(None)),
        queue_depth=await queue.queue_depth(session),
    )

    recent_pipelines = (
        await session.execute(select(PipelineRun).order_by(PipelineRun.created_at.desc()).limit(8))
    ).scalars()
    recent_deployments = (
        await session.execute(select(Deployment).order_by(Deployment.created_at.desc()).limit(8))
    ).scalars()
    recent_incidents = (
        await session.execute(select(Incident).order_by(Incident.created_at.desc()).limit(5))
    ).scalars()

    return OverviewOut(
        stats=stats,
        recent_pipelines=[PipelineOut.model_validate(p) for p in recent_pipelines],
        recent_deployments=[DeploymentOut.model_validate(d) for d in recent_deployments],
        recent_incidents=[IncidentOut.model_validate(i) for i in recent_incidents],
    )


@router.get("/incidents", response_model=list[IncidentOut], summary="List incidents")
async def list_incidents(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.DEPLOYMENT_READ))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[IncidentOut]:
    rows = (
        await session.execute(select(Incident).order_by(Incident.created_at.desc()).limit(limit))
    ).scalars()
    return [IncidentOut.model_validate(i) for i in rows]

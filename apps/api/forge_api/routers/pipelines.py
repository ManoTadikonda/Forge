"""Pipeline creation, inspection, cancellation and logs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from forge_core.authz import Permission
from forge_core.db import queue
from forge_core.db.events import record_audit
from forge_core.db.logs import read_logs
from forge_core.db.models import Artifact, PipelineRun, PipelineStage, Repository
from forge_core.enums import STAGE_SEQUENCE, JobKind, PipelineState
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
    ArtifactOut,
    LogLine,
    Page,
    PipelineCreate,
    PipelineDetail,
    PipelineOut,
    StageOut,
)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


def job_key(run_id: uuid.UUID) -> str:
    return f"pipeline:{run_id}"


async def create_pipeline_run(
    session: AsyncSession,
    *,
    repository: Repository,
    branch: str,
    trigger: str,
    triggered_by_id: uuid.UUID | None,
    idempotency_key: str = "",
) -> tuple[PipelineRun, bool]:
    """Create a run and enqueue its job in one transaction.

    Returns ``(run, created)``. When ``idempotency_key`` matches an existing run for
    the same repository, that run is returned and nothing new is enqueued.
    """
    if idempotency_key:
        existing = (
            await session.execute(
                select(PipelineRun).where(
                    PipelineRun.repository_id == repository.id,
                    PipelineRun.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

    next_number = int(
        await session.scalar(
            select(func.coalesce(func.max(PipelineRun.run_number), 0) + 1).where(
                PipelineRun.repository_id == repository.id
            )
        )
        or 1
    )

    run = PipelineRun(
        id=uuid.uuid4(),
        repository_id=repository.id,
        repository_label=repository.slug,
        run_number=next_number,
        state=PipelineState.QUEUED.value,
        branch=branch or repository.default_branch,
        trigger=trigger,
        triggered_by_id=triggered_by_id,
        idempotency_key=idempotency_key or None,
    )
    session.add(run)
    await session.flush()

    # Pre-create the stage rows so the dashboard can render the full timeline
    # immediately, with not-yet-run stages shown as pending.
    for index, name in enumerate(STAGE_SEQUENCE):
        session.add(PipelineStage(id=uuid.uuid4(), run_id=run.id, name=name.value, sequence=index))

    repository.last_pipeline_at = datetime.now(UTC)
    # Same transaction as the run row: a run can never exist without its job.
    await queue.enqueue(
        session,
        kind=JobKind.RUN_PIPELINE,
        dedupe_key=job_key(run.id),
        payload={"pipeline_run_id": str(run.id)},
        max_attempts=1,  # builds are expensive; a failed build is a result, not a blip
    )
    return run, True


@router.post(
    "",
    response_model=PipelineOut,
    status_code=status.HTTP_201_CREATED,
    summary="Trigger a pipeline",
)
async def trigger_pipeline(
    payload: PipelineCreate,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.PIPELINE_TRIGGER))],
) -> PipelineOut:
    settings = request.app.state.settings
    await enforce_rate_limit(
        request, "pipeline_trigger", limit=settings.rate_limit_write_per_minute
    )

    repository = await session.get(Repository, payload.repository_id)
    if repository is None or not repository.is_active:
        raise NotFoundError("No such repository.")

    running = int(
        await session.scalar(
            select(func.count(PipelineRun.id)).where(
                PipelineRun.repository_id == repository.id,
                PipelineRun.state.notin_(
                    [
                        PipelineState.SUCCEEDED.value,
                        PipelineState.FAILED.value,
                        PipelineState.CANCELED.value,
                    ]
                ),
            )
        )
        or 0
    )
    if running >= settings.max_concurrent_pipelines_per_repository:
        raise ConflictError(
            "A pipeline is already running for this repository.",
            details={"running": running, "limit": settings.max_concurrent_pipelines_per_repository},
        )

    run, created = await create_pipeline_run(
        session,
        repository=repository,
        branch=payload.branch,
        trigger="manual",
        triggered_by_id=user.id,
        idempotency_key=payload.idempotency_key,
    )
    if created:
        await record_audit(
            session,
            action="pipeline.trigger",
            actor_id=user.id,
            actor_label=user.email,
            resource_type="pipeline",
            resource_id=str(run.id),
            detail={"repository": repository.slug, "branch": run.branch},
            ip_address=request.client.host if request.client else "",
            request_id=getattr(request.state, "request_id", ""),
        )
    # The caller polls this run immediately; it must be durable first.
    await commit(session)
    return PipelineOut.model_validate(run)


@router.get("", response_model=Page[PipelineOut], summary="List pipeline runs")
async def list_pipelines(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.PIPELINE_READ))],
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    repository_id: uuid.UUID | None = None,
    state: Annotated[str, Query(max_length=24)] = "",
    branch: Annotated[str, Query(max_length=255)] = "",
) -> Page[PipelineOut]:
    conditions: list[Any] = []
    if repository_id is not None:
        conditions.append(PipelineRun.repository_id == repository_id)
    if state:
        if state not in {s.value for s in PipelineState}:
            raise ValidationError(f"Unknown pipeline state {state!r}.")
        conditions.append(PipelineRun.state == state)
    if branch:
        conditions.append(PipelineRun.branch == branch)

    total = int(await session.scalar(select(func.count(PipelineRun.id)).where(*conditions)) or 0)
    rows = (
        await session.execute(
            select(PipelineRun)
            .where(*conditions)
            .order_by(PipelineRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return Page(
        items=[PipelineOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{pipeline_id}", response_model=PipelineDetail, summary="Inspect a run")
async def get_pipeline(
    pipeline_id: uuid.UUID,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.PIPELINE_READ))],
) -> PipelineDetail:
    run = (
        await session.execute(
            select(PipelineRun)
            .where(PipelineRun.id == pipeline_id)
            # Both relationships are eager-loaded: PipelineDetail declares `stages`
            # and `artifacts`, and pydantic's from_attributes would otherwise trigger
            # a lazy load inside the async context, which raises MissingGreenlet.
            .options(
                selectinload(PipelineRun.stages),
                selectinload(PipelineRun.artifacts),
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("No such pipeline run.")

    artifacts = (
        await session.execute(select(Artifact).where(Artifact.pipeline_run_id == pipeline_id))
    ).scalars()

    detail = PipelineDetail.model_validate(run)
    detail.stages = [
        StageOut.model_validate(s) for s in sorted(run.stages, key=lambda s: s.sequence)
    ]
    detail.artifacts = [ArtifactOut.model_validate(a) for a in artifacts]
    return detail


@router.get("/{pipeline_id}/logs", response_model=list[LogLine], summary="Run logs")
async def get_logs(
    pipeline_id: uuid.UUID,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.PIPELINE_READ))],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[LogLine]:
    run = await session.get(PipelineRun, pipeline_id)
    if run is None:
        raise NotFoundError("No such pipeline run.")
    chunks = await read_logs(
        session,
        owner_type="pipeline",
        owner_id=pipeline_id,
        offset=offset,
        limit=limit,
    )
    return [
        LogLine(
            sequence=c.sequence,
            stage=c.stage,
            content=c.content,
            truncated=c.truncated,
            created_at=c.created_at,
        )
        for c in chunks
    ]


@router.post("/{pipeline_id}/cancel", response_model=PipelineOut, summary="Cancel")
async def cancel_pipeline(
    pipeline_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.PIPELINE_CANCEL))],
) -> PipelineOut:
    run = await session.get(PipelineRun, pipeline_id)
    if run is None:
        raise NotFoundError("No such pipeline run.")

    if PipelineState(run.state) in (
        PipelineState.SUCCEEDED,
        PipelineState.FAILED,
        PipelineState.CANCELED,
    ):
        # Cancellation is idempotent: repeat requests on a finished run are accepted
        # and change nothing, rather than erroring.
        return PipelineOut.model_validate(run)

    run.cancel_requested = True
    await queue.request_cancel(session, job_key(pipeline_id))
    await record_audit(
        session,
        action="pipeline.cancel",
        actor_id=user.id,
        actor_label=user.email,
        resource_type="pipeline",
        resource_id=str(pipeline_id),
        ip_address=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    await commit(session)
    return PipelineOut.model_validate(run)

"""Repository registration and inspection."""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, status
from forge_core.authz import Permission
from forge_core.db.events import record_audit
from forge_core.db.models import PipelineRun, Repository
from forge_core.errors import ConflictError, NotFoundError
from forge_core.urlguard import validate_repo_url
from sqlalchemy import func, or_, select, update

from forge_api.deps import (
    CurrentUserDep,
    DbSession,
    commit,
    enforce_rate_limit,
    require,
)
from forge_api.schemas import (
    Page,
    PipelineOut,
    RepositoryCreate,
    RepositoryDetail,
    RepositoryOut,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])


def _slug(url: str) -> tuple[str, str]:
    """Derive ``host/owner/name`` and a short display name from a repository URL."""
    parts = urlsplit(url)
    path = parts.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    host = (parts.hostname or "unknown").lower()
    slug = f"{host}/{path}" if path else host
    name = path.rsplit("/", 1)[-1] if path else host
    return slug, name


@router.post(
    "", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED,
    summary="Register a repository",
)
async def create_repository(
    payload: RepositoryCreate,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.REPO_WRITE))],
) -> RepositoryOut:
    settings = request.app.state.settings
    await enforce_rate_limit(
        request, "repo_write", limit=settings.rate_limit_write_per_minute
    )
    # Raises UnsafeUrlError (422) for non-HTTPS schemes, embedded credentials, and
    # any host resolving into loopback/private/link-local/metadata ranges.
    validate_repo_url(payload.url, resolve=True)

    slug, derived = _slug(payload.url)
    existing = await session.scalar(
        select(Repository.id).where(Repository.slug == slug)
    )
    if existing is not None:
        raise ConflictError(
            "This repository is already registered.", details={"slug": slug}
        )

    repository = Repository(
        id=uuid.uuid4(),
        name=(payload.name or derived)[:128],
        url=payload.url,
        slug=slug,
        default_branch=payload.default_branch or "main",
        description=payload.description,
        created_by_id=user.id,
    )
    session.add(repository)
    await session.flush()
    await record_audit(
        session, action="repository.create", actor_id=user.id, actor_label=user.email,
        resource_type="repository", resource_id=str(repository.id),
        detail={"url": payload.url, "slug": slug},
        ip_address=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    await commit(session)
    return RepositoryOut.model_validate(repository)


@router.get("", response_model=Page[RepositoryOut], summary="List repositories")
async def list_repositories(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.REPO_READ))],
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str, Query(max_length=200)] = "",
    active_only: bool = True,
) -> Page[RepositoryOut]:
    conditions: list[Any] = []
    if active_only:
        conditions.append(Repository.is_active.is_(True))
    if search:
        pattern = f"%{search.lower()}%"
        conditions.append(
            or_(
                func.lower(Repository.slug).like(pattern),
                func.lower(Repository.name).like(pattern),
            )
        )

    total = int(
        await session.scalar(
            select(func.count(Repository.id)).where(*conditions)
        ) or 0
    )
    rows = (
        await session.execute(
            select(Repository)
            .where(*conditions)
            .order_by(Repository.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return Page(
        items=[RepositoryOut.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/{repository_id}", response_model=RepositoryDetail, summary="Inspect")
async def get_repository(
    repository_id: uuid.UUID,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.REPO_READ))],
) -> RepositoryDetail:
    repository = await session.get(Repository, repository_id)
    if repository is None:
        raise NotFoundError("No such repository.")

    count = int(
        await session.scalar(
            select(func.count(PipelineRun.id)).where(
                PipelineRun.repository_id == repository_id
            )
        ) or 0
    )
    recent = (
        await session.execute(
            select(PipelineRun)
            .where(PipelineRun.repository_id == repository_id)
            .order_by(PipelineRun.created_at.desc())
            .limit(10)
        )
    ).scalars()

    detail = RepositoryDetail.model_validate(repository)
    detail.pipeline_count = count
    detail.recent_pipelines = [PipelineOut.model_validate(p) for p in recent]
    return detail


@router.delete(
    "/{repository_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a repository",
)
async def delete_repository(
    repository_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.REPO_DELETE))],
) -> None:
    repository = await session.get(Repository, repository_id)
    if repository is None:
        raise NotFoundError("No such repository.")

    # Soft delete. Pipeline history, artifacts and audit records reference this row
    # and must remain readable, so the row is deactivated rather than removed.
    repository.is_active = False
    repository.webhook_enabled = False
    # Denormalise the label so pipeline history stays readable once the repository
    # row is gone or deactivated.
    await session.execute(
        update(PipelineRun)
        .where(
            PipelineRun.repository_id == repository_id,
            PipelineRun.repository_label == "",
        )
        .values(repository_label=repository.slug)
    )
    await record_audit(
        session, action="repository.delete", actor_id=user.id, actor_label=user.email,
        resource_type="repository", resource_id=str(repository_id),
        detail={"slug": repository.slug, "mode": "deactivated_history_retained"},
        ip_address=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    await commit(session)

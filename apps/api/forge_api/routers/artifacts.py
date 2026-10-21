"""Artifacts, provenance and SBOM download."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from forge_core.authz import Permission
from forge_core.db.models import Artifact
from forge_core.errors import NotFoundError
from sqlalchemy import func, select

from forge_api.deps import CurrentUserDep, DbSession, require
from forge_api.schemas import ArtifactOut, Page

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("", response_model=Page[ArtifactOut], summary="List artifacts")
async def list_artifacts(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.PIPELINE_READ))],
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    image_name: Annotated[str, Query(max_length=255)] = "",
) -> Page[ArtifactOut]:
    conditions: list[Any] = (
        [Artifact.image_name == image_name] if image_name else []
    )
    total = int(
        await session.scalar(select(func.count(Artifact.id)).where(*conditions)) or 0
    )
    rows = (
        await session.execute(
            select(Artifact)
            .where(*conditions)
            .order_by(Artifact.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return Page(
        items=[ArtifactOut.model_validate(a) for a in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/{artifact_id}", response_model=ArtifactOut, summary="Inspect")
async def get_artifact(
    artifact_id: uuid.UUID,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.PIPELINE_READ))],
) -> ArtifactOut:
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError("No such artifact.")
    return ArtifactOut.model_validate(artifact)


@router.get("/{artifact_id}/sbom", summary="Download the SBOM", response_class=Response)
async def get_sbom(
    artifact_id: uuid.UUID,
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.PIPELINE_READ))],
) -> Response:
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError("No such artifact.")
    if not artifact.sbom_path:
        raise NotFoundError("No SBOM was recorded for this artifact.")

    def _read() -> str:
        path = Path(artifact.sbom_path)
        if not path.is_file():
            raise NotFoundError(
                "The SBOM file is no longer present on disk. Artifact storage may "
                "have been cleaned."
            )
        content = path.read_text(encoding="utf-8")
        json.loads(content)  # fail loudly rather than serving a corrupt document
        return content

    try:
        payload = await asyncio.to_thread(_read)
    except (OSError, json.JSONDecodeError) as exc:
        raise NotFoundError("The stored SBOM could not be read.") from exc

    filename = f"{artifact.image_name}-{artifact.version}.cdx.json"
    return Response(
        content=payload,
        media_type="application/vnd.cyclonedx+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

"""Audit event query API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from forge_core.authz import Permission
from forge_core.db.models import AuditEvent
from sqlalchemy import func, select

from forge_api.deps import CurrentUserDep, DbSession, require
from forge_api.schemas import AuditEventOut, Page

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditEventOut], summary="List audit events")
async def list_audit_events(
    session: DbSession,
    user: Annotated[CurrentUserDep, Depends(require(Permission.AUDIT_READ))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: Annotated[str, Query(max_length=64)] = "",
    outcome: Annotated[str, Query(max_length=16)] = "",
    resource_type: Annotated[str, Query(max_length=48)] = "",
) -> Page[AuditEventOut]:
    conditions: list[Any] = []
    if action:
        conditions.append(AuditEvent.action == action)
    if outcome:
        conditions.append(AuditEvent.outcome == outcome)
    if resource_type:
        conditions.append(AuditEvent.resource_type == resource_type)

    total = int(await session.scalar(select(func.count(AuditEvent.id)).where(*conditions)) or 0)
    rows = (
        await session.execute(
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return Page(
        items=[AuditEventOut.model_validate(e) for e in rows],
        total=total,
        limit=limit,
        offset=offset,
    )

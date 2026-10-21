"""Durable deployment timeline and audit helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge_core.db.models import AuditEvent, DeploymentEvent, Incident
from forge_core.redaction import redact_object


async def record_event(
    session: AsyncSession,
    deployment_id: uuid.UUID,
    *,
    kind: str,
    message: str,
    from_state: str = "",
    to_state: str = "",
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> DeploymentEvent:
    """Append one ordered event to a deployment's timeline."""
    next_sequence = (
        await session.scalar(
            select(func.coalesce(func.max(DeploymentEvent.sequence), -1) + 1).where(
                DeploymentEvent.deployment_id == deployment_id
            )
        )
    ) or 0
    cleaned = redact_object(payload or {})
    event = DeploymentEvent(
        id=uuid.uuid4(),
        deployment_id=deployment_id,
        sequence=int(next_sequence),
        kind=kind,
        from_state=from_state,
        to_state=to_state,
        message=message[:4000],
        actor=actor,
        payload=cleaned if isinstance(cleaned, dict) else {},
        created_at=datetime.now(UTC),
    )
    session.add(event)
    return event


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_label: str = "system",
    actor_id: uuid.UUID | None = None,
    resource_type: str = "",
    resource_id: str = "",
    outcome: str = "SUCCESS",
    detail: dict[str, Any] | None = None,
    ip_address: str = "",
    user_agent: str = "",
    request_id: str = "",
) -> AuditEvent:
    cleaned = redact_object(detail or {})
    event = AuditEvent(
        id=uuid.uuid4(),
        actor_id=actor_id,
        actor_label=actor_label[:320],
        action=action[:64],
        resource_type=resource_type[:48],
        resource_id=str(resource_id)[:64],
        outcome=outcome,
        ip_address=ip_address[:64],
        user_agent=user_agent[:256],
        request_id=request_id[:64],
        detail=cleaned if isinstance(cleaned, dict) else {},
        created_at=datetime.now(UTC),
    )
    session.add(event)
    return event


async def raise_incident(
    session: AsyncSession,
    *,
    deployment_id: uuid.UUID | None,
    service_id: uuid.UUID | None,
    title: str,
    detail: str,
    kind: str = "ROLLBACK",
    severity: str = "HIGH",
    verdict: str = "UNHEALTHY",
    evidence: dict[str, Any] | None = None,
) -> Incident:
    """Create the observable alert record that accompanies a rollback."""
    cleaned = redact_object(evidence or {})
    incident = Incident(
        id=uuid.uuid4(),
        deployment_id=deployment_id,
        service_id=service_id,
        kind=kind,
        severity=severity,
        title=title[:255],
        detail=detail[:4000],
        verdict=verdict,
        evidence=cleaned if isinstance(cleaned, dict) else {},
    )
    session.add(incident)
    return incident


__all__ = ["raise_incident", "record_audit", "record_event"]

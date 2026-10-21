"""Bounded log storage for pipeline and deployment output."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge_core.db.models import LogChunk
from forge_core.redaction import redact_text

#: Output is split into chunks so no single row grows without bound and the API can
#: paginate without loading an entire build's output.
CHUNK_BYTES = 64 * 1024


async def append_log(
    session: AsyncSession,
    *,
    owner_type: str,
    owner_id: uuid.UUID,
    stage: str,
    content: str,
    max_total_bytes: int = 4 * 1024 * 1024,
) -> int:
    """Append redacted output, enforcing a total size ceiling. Returns chunks written."""
    cleaned = redact_text(content)
    if not cleaned:
        return 0

    used = (
        await session.scalar(
            select(func.coalesce(func.sum(LogChunk.byte_size), 0)).where(
                LogChunk.owner_type == owner_type, LogChunk.owner_id == owner_id
            )
        )
    ) or 0
    next_sequence = (
        await session.scalar(
            select(func.coalesce(func.max(LogChunk.sequence), -1) + 1).where(
                LogChunk.owner_type == owner_type, LogChunk.owner_id == owner_id
            )
        )
    ) or 0

    payload = cleaned.encode("utf-8")
    remaining = max(0, max_total_bytes - int(used))
    truncated = False
    if len(payload) > remaining:
        payload = payload[:remaining]
        truncated = True
        if remaining == 0:
            # Already at the ceiling: record the fact once, not the output.
            session.add(
                LogChunk(
                    id=uuid.uuid4(), owner_type=owner_type, owner_id=owner_id,
                    stage=stage, sequence=int(next_sequence),
                    content="[forge] log size limit reached; further output discarded.",
                    byte_size=0, truncated=True, created_at=datetime.now(UTC),
                )
            )
            return 1

    written = 0
    for offset in range(0, len(payload), CHUNK_BYTES):
        piece = payload[offset : offset + CHUNK_BYTES]
        session.add(
            LogChunk(
                id=uuid.uuid4(),
                owner_type=owner_type,
                owner_id=owner_id,
                stage=stage,
                sequence=int(next_sequence) + written,
                content=piece.decode("utf-8", errors="replace"),
                byte_size=len(piece),
                truncated=truncated and offset + CHUNK_BYTES >= len(payload),
                created_at=datetime.now(UTC),
            )
        )
        written += 1
    return written


async def read_logs(
    session: AsyncSession,
    *,
    owner_type: str,
    owner_id: uuid.UUID,
    offset: int = 0,
    limit: int = 200,
) -> list[LogChunk]:
    result = await session.execute(
        select(LogChunk)
        .where(LogChunk.owner_type == owner_type, LogChunk.owner_id == owner_id)
        .order_by(LogChunk.sequence)
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars())


__all__ = ["CHUNK_BYTES", "append_log", "read_logs"]

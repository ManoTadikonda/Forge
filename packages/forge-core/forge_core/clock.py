"""Time sources."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class SystemClock:
    """Wall-clock time, always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Manually advanced clock for deterministic tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["FrozenClock", "SystemClock", "utcnow"]

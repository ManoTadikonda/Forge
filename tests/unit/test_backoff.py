"""Retry backoff."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest
from forge_core.backoff import backoff_seconds, next_attempt_at


def test_grows_exponentially_without_jitter() -> None:
    delays = [backoff_seconds(n, base=2.0, cap=1000, jitter=False) for n in range(1, 6)]
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0]


def test_respects_the_cap() -> None:
    assert backoff_seconds(50, base=2.0, cap=300, jitter=False) == 300


def test_very_large_attempt_does_not_overflow() -> None:
    """The exponent is clamped, so a runaway attempt count cannot raise."""
    assert backoff_seconds(100_000, base=2.0, cap=300, jitter=False) == 300


def test_jitter_stays_within_bounds() -> None:
    rng = random.Random(1234)
    for attempt in range(1, 10):
        value = backoff_seconds(attempt, base=2.0, cap=60, jitter=True, rng=rng)
        assert 0.0 <= value <= 60


def test_jitter_actually_varies() -> None:
    """Full jitter exists to desynchronise retries; identical delays defeat it."""
    rng = random.Random(7)
    values = {backoff_seconds(5, base=2.0, cap=60, rng=rng) for _ in range(25)}
    assert len(values) > 10


def test_rejects_attempt_below_one() -> None:
    with pytest.raises(ValueError, match="must be >= 1"):
        backoff_seconds(0)


def test_next_attempt_at_moves_forward() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert next_attempt_at(now, 1, base=2.0, jitter=False) > now

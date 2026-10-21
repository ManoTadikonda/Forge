"""Retry scheduling: exponential backoff with full jitter.

Full jitter (``random.uniform(0, capped)``) is used rather than equal jitter because
Forge retries are driven by many workers polling the same queue; spreading retries
uniformly avoids synchronised thundering herds after a shared dependency recovers.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta


def backoff_seconds(
    attempt: int,
    *,
    base: float = 2.0,
    cap: float = 300.0,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> float:
    """Return the delay before retry number ``attempt`` (1-based).

    ``attempt=1`` is the delay after the first failure.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    # 2 ** (attempt - 1) grows unboundedly; clamp the exponent before the power
    # so very large attempt counts cannot produce an OverflowError.
    exponent = min(attempt - 1, 32)
    raw = base * (2.0**exponent)
    capped = min(raw, cap)
    if not jitter:
        return capped
    generator = rng or random
    return generator.uniform(0.0, capped)


def next_attempt_at(
    now: datetime,
    attempt: int,
    *,
    base: float = 2.0,
    cap: float = 300.0,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> datetime:
    return now + timedelta(
        seconds=backoff_seconds(attempt, base=base, cap=cap, jitter=jitter, rng=rng)
    )


__all__ = ["backoff_seconds", "next_attempt_at"]

"""Weighted selection.

Smooth weighted round-robin (the nginx algorithm) is used rather than random
selection. The distinction matters for this project: with randomness, a 10% canary
only approaches 10% in expectation, and verifying the split needs statistics. With
SWRR the distribution is *exact* over each cycle, so a test can assert that 100
requests at weight 10 produced exactly 10 hits on the candidate.

The algorithm also spreads selections evenly rather than emitting runs of the same
backend, which avoids bursty load on a brand-new candidate.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass
class _Backend:
    key: str
    weight: int
    current: int = 0
    requests: int = 0
    errors: int = 0


@dataclass
class WeightedBalancer:
    """Thread-safe smooth weighted round-robin over release keys."""

    _backends: dict[str, _Backend] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set_weights(self, weights: Mapping[str, int]) -> None:
        """Replace the weight table.

        Counters for backends that remain are preserved, so traffic accounting is
        continuous across a canary step change.
        """
        with self._lock:
            for key, weight in weights.items():
                if weight < 0:
                    raise ValueError(f"Weight for {key!r} must not be negative")
                existing = self._backends.get(key)
                if existing is None:
                    self._backends[key] = _Backend(key=key, weight=int(weight))
                else:
                    existing.weight = int(weight)
                    # Reset the smoothing accumulator so a new split takes effect
                    # immediately rather than inheriting the previous cycle's debt.
                    existing.current = 0
            for key in list(self._backends):
                if key not in weights:
                    del self._backends[key]

    def weights(self) -> dict[str, int]:
        with self._lock:
            return {k: b.weight for k, b in self._backends.items()}

    def total_weight(self) -> int:
        with self._lock:
            return sum(b.weight for b in self._backends.values())

    def select(self) -> str | None:
        """Return the next backend key, or None when nothing can serve traffic."""
        with self._lock:
            eligible = [b for b in self._backends.values() if b.weight > 0]
            if not eligible:
                return None
            total = sum(b.weight for b in eligible)
            for backend in eligible:
                backend.current += backend.weight
            chosen = max(eligible, key=lambda b: b.current)
            chosen.current -= total
            return chosen.key

    def record(self, key: str, *, error: bool) -> None:
        with self._lock:
            backend = self._backends.get(key)
            if backend is None:
                return
            backend.requests += 1
            if error:
                backend.errors += 1

    def stats(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                k: {"weight": b.weight, "requests": b.requests, "errors": b.errors}
                for k, b in self._backends.items()
            }

    def reset_counters(self) -> None:
        with self._lock:
            for backend in self._backends.values():
                backend.requests = 0
                backend.errors = 0


__all__ = ["WeightedBalancer"]

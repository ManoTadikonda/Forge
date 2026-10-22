"""Weighted traffic balancing."""

from __future__ import annotations

from collections import Counter

import pytest
from forge_router.balancer import WeightedBalancer


@pytest.mark.parametrize(
    "weights",
    [
        {"stable": 90, "candidate": 10},
        {"stable": 75, "candidate": 25},
        {"stable": 50, "candidate": 50},
        {"stable": 0, "candidate": 100},
        {"a": 60, "b": 30, "c": 10},
    ],
)
def test_distribution_is_exact_over_a_cycle(weights: dict[str, int]) -> None:
    """Smooth weighted round-robin is deterministic: 10% means exactly 10%."""
    balancer = WeightedBalancer()
    balancer.set_weights(weights)
    picks = Counter(balancer.select() for _ in range(100))
    for key, weight in weights.items():
        assert picks.get(key, 0) == weight


def test_selection_is_spread_not_clustered() -> None:
    """A new candidate should not receive its entire share as one burst."""
    balancer = WeightedBalancer()
    balancer.set_weights({"stable": 80, "candidate": 20})
    sequence = [balancer.select() for _ in range(20)]
    positions = [i for i, key in enumerate(sequence) if key == "candidate"]
    assert len(positions) == 4
    assert max(positions) - min(positions) >= 10


def test_zero_weight_backend_receives_nothing() -> None:
    balancer = WeightedBalancer()
    balancer.set_weights({"stable": 100, "candidate": 0})
    assert {balancer.select() for _ in range(50)} == {"stable"}


def test_no_backends_selects_nothing() -> None:
    assert WeightedBalancer().select() is None


def test_all_zero_weights_selects_nothing() -> None:
    """Better to serve 503 than to pick a backend no one asked to receive traffic."""
    balancer = WeightedBalancer()
    balancer.set_weights({"a": 0, "b": 0})
    assert balancer.select() is None


def test_negative_weight_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        WeightedBalancer().set_weights({"a": -1})


def test_counters_survive_a_weight_change() -> None:
    """Traffic accounting must be continuous across a canary step."""
    balancer = WeightedBalancer()
    balancer.set_weights({"a": 100, "b": 0})
    for _ in range(10):
        balancer.record(balancer.select(), error=False)
    balancer.set_weights({"a": 50, "b": 50})
    assert balancer.stats()["a"]["requests"] == 10


def test_removed_backend_disappears_from_the_table() -> None:
    balancer = WeightedBalancer()
    balancer.set_weights({"a": 50, "b": 50})
    balancer.set_weights({"a": 100})
    assert set(balancer.weights()) == {"a"}


def test_errors_are_counted_separately() -> None:
    balancer = WeightedBalancer()
    balancer.set_weights({"a": 100})
    balancer.record("a", error=True)
    balancer.record("a", error=False)
    assert balancer.stats()["a"] == {"weight": 100, "requests": 2, "errors": 1}


def test_recording_an_unknown_backend_is_ignored() -> None:
    balancer = WeightedBalancer()
    balancer.set_weights({"a": 100})
    balancer.record("ghost", error=True)
    assert "ghost" not in balancer.stats()


def test_reset_counters_keeps_weights() -> None:
    balancer = WeightedBalancer()
    balancer.set_weights({"a": 100})
    balancer.record("a", error=True)
    balancer.reset_counters()
    assert balancer.stats()["a"] == {"weight": 100, "requests": 0, "errors": 0}

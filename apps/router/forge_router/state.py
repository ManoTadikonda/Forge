"""Router routing table.

The router is deliberately *not* the source of truth. It holds the live routing table
in memory and the control plane reasserts it; durable deployment state lives in
PostgreSQL. On restart the router comes up empty and the controller re-applies the
table, which is the correct direction of authority.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field

from forge_router.balancer import WeightedBalancer


@dataclass(frozen=True, slots=True)
class Target:
    """One routable release."""

    release_id: str
    version: str
    url: str


@dataclass
class ServiceRoute:
    service: str
    environment: str
    targets: dict[str, Target] = field(default_factory=dict)
    balancer: WeightedBalancer = field(default_factory=WeightedBalancer)

    @property
    def key(self) -> str:
        return route_key(self.service, self.environment)


def route_key(service: str, environment: str) -> str:
    return f"{service}:{environment}"


class RoutingTable:
    def __init__(self) -> None:
        self._routes: dict[str, ServiceRoute] = {}
        self._lock = threading.Lock()

    def upsert(
        self,
        service: str,
        environment: str,
        targets: Mapping[str, Target],
        weights: Mapping[str, int],
    ) -> ServiceRoute:
        key = route_key(service, environment)
        with self._lock:
            route = self._routes.get(key)
            if route is None:
                route = ServiceRoute(service=service, environment=environment)
                self._routes[key] = route
            route.targets = dict(targets)
            route.balancer.set_weights(weights)
            return route

    def set_weights(self, service: str, environment: str,
                    weights: Mapping[str, int]) -> ServiceRoute | None:
        with self._lock:
            route = self._routes.get(route_key(service, environment))
        if route is None:
            return None
        unknown = set(weights) - set(route.targets)
        if unknown:
            raise KeyError(f"Unknown release ids for this route: {sorted(unknown)}")
        route.balancer.set_weights(weights)
        return route

    def get(self, service: str, environment: str) -> ServiceRoute | None:
        with self._lock:
            return self._routes.get(route_key(service, environment))

    def remove_target(self, service: str, environment: str, release_id: str) -> bool:
        route = self.get(service, environment)
        if route is None or release_id not in route.targets:
            return False
        remaining = {k: v for k, v in route.balancer.weights().items() if k != release_id}
        del route.targets[release_id]
        route.balancer.set_weights(remaining)
        return True

    def delete(self, service: str, environment: str) -> bool:
        with self._lock:
            return self._routes.pop(route_key(service, environment), None) is not None

    def all(self) -> list[ServiceRoute]:
        with self._lock:
            return list(self._routes.values())


__all__ = ["RoutingTable", "ServiceRoute", "Target", "route_key"]

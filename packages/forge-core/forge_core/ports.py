"""Ports (interfaces) separating orchestration from infrastructure.

The deployment controller, pipeline runner, health analyser and rollback logic depend
only on these protocols. Each has a process-based adapter that runs on any host with
Python, and a container-based adapter for hosts with a container runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class BuildRequest:
    workspace: Path
    repository_url: str
    commit_sha: str
    image_name: str
    version: str
    build_args: Mapping[str, str] = field(default_factory=dict)
    dockerfile: str = "Dockerfile"
    timeout_seconds: int = 900


@dataclass(frozen=True, slots=True)
class BuildResult:
    """The output of a build.

    ``digest`` is the immutable content identifier (``sha256:...``). Forge deploys by
    digest only; ``tags`` are informational and are never used to resolve a release.
    """

    digest: str
    size_bytes: int
    artifact_path: Path
    tags: tuple[str, ...] = field(default=())
    logs: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class ArtifactBuilder(Protocol):
    """Turns a checked-out workspace into an immutable, content-addressed artifact."""

    name: str

    async def available(self) -> tuple[bool, str]:
        """Return ``(usable, reason)``. Adapters must not raise from this probe."""
        ...

    async def build(self, request: BuildRequest) -> BuildResult: ...


@dataclass(frozen=True, slots=True)
class PublishResult:
    reference: str
    digest: str
    registry: str
    signed: bool = False
    #: Populated only when a real signature/attestation was produced.
    signature_reference: str | None = None


@runtime_checkable
class ArtifactRegistry(Protocol):
    """Stores artifacts and resolves them by immutable digest."""

    name: str

    async def available(self) -> tuple[bool, str]: ...

    async def publish(
        self, result: BuildResult, *, image_name: str, version: str
    ) -> PublishResult: ...

    async def resolve(self, digest: str) -> Path | None:
        """Return a local path for ``digest``, or None when it is not present."""
        ...


@dataclass(frozen=True, slots=True)
class ReleaseSpec:
    """Everything needed to start one release of one service."""

    release_id: str
    service_name: str
    environment: str
    version: str
    #: Immutable artifact digest. Never a mutable tag.
    image_digest: str
    artifact_path: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    replicas: int = 1
    health_path: str = "/health"
    ready_path: str = "/ready"
    metrics_path: str = "/metrics"


@dataclass(frozen=True, slots=True)
class ReleaseHandle:
    """A running release."""

    release_id: str
    version: str
    image_digest: str
    endpoints: tuple[str, ...]
    #: Adapter-specific identifiers (PIDs, container ids) for cleanup and auditing.
    instances: tuple[str, ...] = field(default=())
    started_at: datetime | None = None

    @property
    def primary_endpoint(self) -> str:
        if not self.endpoints:
            raise ValueError(f"Release {self.release_id} has no endpoints")
        return self.endpoints[0]


@runtime_checkable
class DeploymentAdapter(Protocol):
    """Starts, probes, routes to and stops releases.

    Implementations must be idempotent: starting an already-running release returns the
    existing handle, and stopping an absent release succeeds.
    """

    name: str

    async def available(self) -> tuple[bool, str]: ...

    async def start_release(self, spec: ReleaseSpec) -> ReleaseHandle: ...

    async def wait_ready(self, handle: ReleaseHandle, *, timeout_seconds: int) -> bool: ...

    async def probe(self, handle: ReleaseHandle) -> Mapping[str, object]:
        """Scrape one health/metrics observation from the release."""
        ...

    async def set_traffic_weights(
        self, service_name: str, environment: str, weights: Mapping[str, int]
    ) -> None:
        """Apply routing weights, keyed by release id. Values must sum to 100."""
        ...

    async def get_traffic_weights(
        self, service_name: str, environment: str
    ) -> Mapping[str, int]: ...

    async def stop_release(self, handle: ReleaseHandle) -> None: ...

    async def list_releases(
        self, service_name: str, environment: str
    ) -> Sequence[ReleaseHandle]: ...


@runtime_checkable
class KeyValueStore(Protocol):
    """Ephemeral coordination: rate-limit counters, caches, advisory hints.

    Never the source of truth for pipeline or deployment state.
    """

    name: str

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def incr(self, key: str, *, ttl_seconds: int | None = None) -> int: ...
    async def close(self) -> None: ...


class Clock(Protocol):
    """Injectable time source so time-dependent logic is testable."""

    def now(self) -> datetime: ...


__all__ = [
    "ArtifactBuilder",
    "ArtifactRegistry",
    "BuildRequest",
    "BuildResult",
    "Clock",
    "DeploymentAdapter",
    "KeyValueStore",
    "PublishResult",
    "ReleaseHandle",
    "ReleaseSpec",
]

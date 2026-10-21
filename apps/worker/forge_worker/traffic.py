"""Synthetic canary probe traffic.

During an observation window the controller issues real HTTP requests through the
router to the service under rollout. This is active canary analysis — the same idea as
a load-testing sidecar in Flagger or Argo Rollouts — and it exists because a health
verdict needs a minimum sample count before it means anything.

It supplements organic traffic rather than replacing it: requests go through the real
router, are split by the real weights, and are served by the real releases, so the
measurements they produce are genuine. What is synthetic is the *demand*, not the
measurement.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class TrafficResult:
    sent: int
    succeeded: int
    failed: int
    by_version: dict[str, int]
    by_release: dict[str, int]

    @property
    def error_rate(self) -> float:
        return (self.failed / self.sent) if self.sent else 0.0


async def generate(
    client: httpx.AsyncClient,
    url: str,
    *,
    count: int,
    concurrency: int = 8,
    timeout_seconds: float = 5.0,
) -> TrafficResult:
    """Issue ``count`` requests to ``url`` and attribute each to the serving release."""
    semaphore = asyncio.Semaphore(max(1, concurrency))
    by_version: dict[str, int] = {}
    by_release: dict[str, int] = {}
    succeeded = 0
    failed = 0
    lock = asyncio.Lock()

    async def _one() -> None:
        nonlocal succeeded, failed
        async with semaphore:
            try:
                response = await client.get(url, timeout=timeout_seconds)
                version = response.headers.get("X-Forge-Version", "unknown")
                release = response.headers.get("X-Forge-Release", "unknown")
                ok = response.status_code < 500
            except httpx.RequestError:
                version, release, ok = "unreachable", "unreachable", False
            async with lock:
                by_version[version] = by_version.get(version, 0) + 1
                by_release[release] = by_release.get(release, 0) + 1
                if ok:
                    succeeded += 1
                else:
                    failed += 1

    await asyncio.gather(*(_one() for _ in range(max(0, count))))
    return TrafficResult(count, succeeded, failed, by_version, by_release)


__all__ = ["TrafficResult", "generate"]

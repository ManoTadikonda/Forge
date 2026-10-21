"""Rate limiting.

Uses Redis when configured so limits hold across API replicas, and falls back to an
in-process counter otherwise. The fallback is correct for a single process and is
reported as such — a per-process limit on a multi-replica deployment is weaker than
it looks, so the health endpoint surfaces which backend is active.

Algorithm: fixed window. Simple, cheap, and adequate for abuse control. It permits up
to 2x the limit across a window boundary; a sliding window would be the upgrade if
that mattered.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from forge_core.logging import get_logger

logger = get_logger("forge.api.ratelimit")


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    limit: int
    reset_seconds: int


class InMemoryRateLimiter:
    name = "in-memory"

    def __init__(self) -> None:
        self._counters: dict[tuple[str, int], int] = {}

    async def check(self, key: str, *, limit: int, window_seconds: int = 60
                    ) -> RateLimitDecision:
        bucket = int(time.time() // window_seconds)
        composite = (key, bucket)
        # Drop counters from previous windows so the dict cannot grow without bound.
        if len(self._counters) > 10_000:
            self._counters = {
                k: v for k, v in self._counters.items() if k[1] >= bucket - 1
            }
        count = self._counters.get(composite, 0) + 1
        self._counters[composite] = count
        reset = window_seconds - int(time.time() % window_seconds)
        return RateLimitDecision(count <= limit, max(0, limit - count), limit, reset)

    async def close(self) -> None:
        self._counters.clear()


class RedisRateLimiter:
    name = "redis"

    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        # redis-py's from_url is untyped; the client is used through a narrow
        # surface (pipeline/incr/expire/ping/aclose) that this module owns.
        self._client: Any = redis.from_url(  # type: ignore[no-untyped-call]
            url, decode_responses=True
        )

    async def check(self, key: str, *, limit: int, window_seconds: int = 60
                    ) -> RateLimitDecision:
        bucket = int(time.time() // window_seconds)
        composite = f"forge:ratelimit:{key}:{bucket}"
        try:
            pipe = self._client.pipeline()
            pipe.incr(composite)
            pipe.expire(composite, window_seconds + 1)
            count, _ = await pipe.execute()
        except Exception:
            # Availability over strictness: a Redis outage must not take down the API.
            # The event is logged so the gap is visible.
            logger.warning("ratelimit_backend_unavailable", key=key)
            return RateLimitDecision(True, limit, limit, window_seconds)
        reset = window_seconds - int(time.time() % window_seconds)
        return RateLimitDecision(
            int(count) <= limit, max(0, limit - int(count)), limit, reset
        )

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


def build_rate_limiter(redis_url: str) -> InMemoryRateLimiter | RedisRateLimiter:
    if redis_url:
        try:
            return RedisRateLimiter(redis_url)
        except Exception:
            logger.warning("redis_rate_limiter_unavailable_falling_back")
    return InMemoryRateLimiter()


__all__ = [
    "InMemoryRateLimiter",
    "RateLimitDecision",
    "RedisRateLimiter",
    "build_rate_limiter",
]

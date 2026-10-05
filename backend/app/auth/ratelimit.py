"""In-memory sliding-window rate limiter (per process).

v1 runs one uvicorn process, so in-memory is enough; sharing limits across processes or
replicas is #24. Keys are client IPs; nothing is logged here.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import timedelta

from app.auth.clock import Clock


class RateLimiter:
    """At most `limit` hits per `window` and key."""

    def __init__(self, limit: int, window: timedelta, clock: Clock) -> None:
        self.limit = limit
        self.window = window.total_seconds()
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _now(self) -> float:
        return self.clock.now().timestamp()

    def _recent(self, key: str, now: float) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            return deque()
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if not hits:
            del self._hits[key]
        return hits

    def retry_after(self, key: str) -> int | None:
        """Seconds until the next hit is allowed, or `None` if allowed now."""
        now = self._now()
        hits = self._recent(key, now)
        if len(hits) < self.limit:
            return None
        return max(1, math.ceil(hits[0] + self.window - now))

    def add(self, key: str) -> None:
        now = self._now()
        self._recent(key, now)
        self._hits.setdefault(key, deque()).append(now)

    def hit(self, key: str) -> int | None:
        """Count one hit if allowed. Returns `None` if counted, else the `Retry-After` seconds."""
        wait = self.retry_after(key)
        if wait is None:
            self.add(key)
        return wait

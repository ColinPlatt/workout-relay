"""Small in-process rate limiter for sensitive endpoints.

Deployments with multiple instances should replace this with a shared Redis
limiter at the ingress layer as described in docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self):
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        cutoff = time.monotonic() - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(time.monotonic())
            return True

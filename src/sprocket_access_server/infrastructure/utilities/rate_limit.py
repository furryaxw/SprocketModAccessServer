from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from ...domain.errors import ApiError


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(
            self,
            *,
            scope: str,
            subject: str,
            limit: int,
            window: int,
            now: float | None = None,
    ) -> None:
        if not scope or not subject or limit < 1 or window < 1:
            raise ValueError("rate limit configuration is invalid")
        timestamp = time.monotonic() if now is None else now
        key = (scope, subject)
        with self._lock:
            events = self._events[key]
            cutoff = timestamp - window
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, math.ceil(window - (timestamp - events[0])))
                raise ApiError(
                    429,
                    "rate_limit_exceeded",
                    "too many requests; retry later",
                    (("Retry-After", str(retry_after)),),
                )
            events.append(timestamp)

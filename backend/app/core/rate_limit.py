"""A bounded, in-process, fixed-window rate limiter.

Good enough for one process, which is all development and the demo run. It is NOT a production
limiter for several workers or instances:
- each worker counts separately, so the effective limit is the limit times the worker count;
- counts reset when the process restarts.
Production needs a shared store (the planned `rate_limit_counters` table) or limits at the proxy.
See SECURITY.md §13.1.

Keys are kept only in memory and never logged. When full, the oldest windows are evicted, which
favours availability: a flood of distinct clients can reset others' counters, but can't exhaust
memory or lock everyone out.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Limit:
    requests: int
    window_seconds: int


class RateLimiter:
    def __init__(
        self, *, max_keys: int = 10_000, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (window start, count, window length). Least recently used first.
        self._windows: OrderedDict[str, tuple[float, int, int]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._windows)

    def hit(self, key: str, limit: Limit) -> int | None:
        """Counts one request. Returns None if allowed, or seconds until the window resets."""
        now = self._clock()
        with self._lock:
            started, count, _ = self._windows.get(key, (now, 0, limit.window_seconds))
            if now - started >= limit.window_seconds:
                started, count = now, 0
            count += 1
            self._windows[key] = (started, count, limit.window_seconds)
            self._windows.move_to_end(key)
            self._evict(now)
            if count > limit.requests:
                return max(1, int(started + limit.window_seconds - now + 0.999))
            return None

    def _evict(self, now: float) -> None:
        """Drops expired windows from the least recently used end, then any excess."""
        while self._windows:
            key, (started, _, window) = next(iter(self._windows.items()))
            if now - started < window and len(self._windows) <= self._max_keys:
                break
            del self._windows[key]

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()

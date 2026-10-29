"""In-process failure throttling (core/rate_limit.py).

Used by the public authentication endpoints (login and account activation) to
slow down password and token guessing. It counts FAILED attempts per key in a
sliding time window; once a key reaches the limit it is blocked until its
oldest failure leaves the window.

Limitations (documented on purpose): the state lives in this process only, so
it resets on restart and is not shared between several workers. A production
deployment behind multiple workers should add a reverse-proxy or shared-store
rate limiter in front of these endpoints.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable

#: Upper bound on tracked keys, so a flood of distinct keys cannot grow memory
#: without limit. When reached, keys with no recent failures are pruned first.
_MAX_TRACKED_KEYS = 10_000


class FailureLimiter:
    """Block a key after ``max_failures`` failures within ``window_seconds``."""

    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[Hashable, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: Hashable, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window_seconds]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def is_blocked(self, key: Hashable) -> bool:
        """True when ``key`` has reached the failure limit in the window."""
        with self._lock:
            return len(self._recent(key, self._clock())) >= self.max_failures

    def record_failure(self, key: Hashable) -> None:
        """Remember one failed attempt for ``key``."""
        with self._lock:
            now = self._clock()
            if len(self._failures) >= _MAX_TRACKED_KEYS:
                for stale in list(self._failures):
                    self._recent(stale, now)
            self._recent(key, now)
            self._failures.setdefault(key, []).append(now)

    def reset(self, key: Hashable) -> None:
        """Forget the failures for ``key`` (e.g. after a successful login)."""
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        """Forget everything (used by tests)."""
        with self._lock:
            self._failures.clear()

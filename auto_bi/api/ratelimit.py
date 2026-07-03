"""In-process login rate limiter (B-3): a single-worker deployment (workers=1 — S08
rationale is in-memory sessions/SSE) can hold this as plain process memory, no Redis
needed. pbkdf2 already slows one password check; this throttles the *series* of
requests an online brute-force sends.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class LoginRateLimiter:
    """Sliding window of `RATE_LIMIT` attempts per `WINDOW_SECONDS` per key (client IP).

    Once a key exceeds the window, it is locked out; each further call while still
    locked out grows the *next* lockout (doubling per strike, capped at
    `LOCKOUT_CAP_SECONDS`), so a scripted retry-loop gets throttled progressively harder
    while a one-off accidental burst only waits out the base lockout. Strikes are never
    reset, so an attacker cannot wait once and reset back to a short lockout.
    """

    RATE_LIMIT = 5
    WINDOW_SECONDS = 60.0
    BASE_LOCKOUT_SECONDS = 30.0
    LOCKOUT_CAP_SECONDS = 900.0  # 15 minutes

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}
        self._lockout_until: dict[str, float] = {}
        self._strikes: dict[str, int] = {}

    def check(self, key: str) -> float:
        """Record one attempt for `key`. Returns seconds to wait (0.0 = allowed)."""
        now = self._clock()
        with self._lock:
            locked_until = self._lockout_until.get(key, 0.0)
            if now < locked_until:
                return locked_until - now
            recent = [t for t in self._attempts.get(key, []) if now - t < self.WINDOW_SECONDS]
            recent.append(now)
            if len(recent) > self.RATE_LIMIT:
                strikes = self._strikes.get(key, 0) + 1
                self._strikes[key] = strikes
                lockout = min(
                    self.BASE_LOCKOUT_SECONDS * (2 ** (strikes - 1)), self.LOCKOUT_CAP_SECONDS
                )
                self._lockout_until[key] = now + lockout
                self._attempts[key] = []
                return lockout
            self._attempts[key] = recent
            return 0.0

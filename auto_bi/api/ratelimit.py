"""In-process sliding-window rate limiter: a single-worker deployment (workers=1 — S08
rationale is in-memory sessions/SSE) can hold this as plain process memory, no Redis
needed.

Two call sites reuse the same mechanism with different parameters:
- `LoginRateLimiter` (B-3): 5 attempts/60s/IP on `/api/v1/auth/login`. pbkdf2 already
  slows one password check; this throttles the *series* of requests an online
  brute-force sends.
- the O-2 session-quota gate (`auto_bi.api.app`): a much larger, day-scale window that
  protects the LLM budget from runaway per-IP usage on the session-creating endpoints,
  not from a fast brute-force script — see `app.py` for the construction.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class LoginRateLimiter:
    """Sliding window of `rate_limit` attempts per `window_seconds` per key (e.g. client
    IP). Defaults match the original login-hardening policy (B-3); pass explicit
    parameters to reuse the same mechanism for a different window/lockout scale (e.g. a
    per-day LLM-call quota).

    Once a key exceeds the window, it is locked out; each further call while still
    locked out grows the *next* lockout (doubling per strike, capped at
    `lockout_cap_seconds`), so a scripted retry-loop gets throttled progressively harder
    while a one-off accidental burst only waits out the base lockout. Strikes are never
    reset while the key stays active, so an attacker cannot wait once and reset back to
    a short lockout. Passing a `lockout_cap_seconds` equal to `base_lockout_seconds`
    disables the escalation (every violation gets the same flat lockout) — appropriate
    for a budget quota, where the point is a hard cap, not deterring a repeat attacker.

    Memory (L-4): keys idle longer than `max(window_seconds, lockout_cap_seconds)` are
    purged (amortized: a scan at most once per PURGE_INTERVAL_SECONDS, piggybacked on
    check()), so a public exposure does not grow process memory with every unique IP
    ever seen. Purging forgets strikes too — the deliberate trade-off is that resetting
    the escalation requires staying silent at least as long as the maximum lockout, so
    an attacker gains nothing over just serving the capped lockout.
    """

    RATE_LIMIT = 5
    WINDOW_SECONDS = 60.0
    BASE_LOCKOUT_SECONDS = 30.0
    LOCKOUT_CAP_SECONDS = 900.0  # 15 minutes
    PURGE_INTERVAL_SECONDS = 60.0  # L-4: scan for stale keys at most this often

    def __init__(
        self,
        *,
        rate_limit: int | None = None,
        window_seconds: float | None = None,
        base_lockout_seconds: float | None = None,
        lockout_cap_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate_limit = self.RATE_LIMIT if rate_limit is None else rate_limit
        self.window_seconds = self.WINDOW_SECONDS if window_seconds is None else window_seconds
        self.base_lockout_seconds = (
            self.BASE_LOCKOUT_SECONDS if base_lockout_seconds is None else base_lockout_seconds
        )
        self.lockout_cap_seconds = (
            self.LOCKOUT_CAP_SECONDS if lockout_cap_seconds is None else lockout_cap_seconds
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}
        self._lockout_until: dict[str, float] = {}
        self._strikes: dict[str, int] = {}
        self._last_seen: dict[str, float] = {}
        self._last_purge = clock()

    def check(self, key: str) -> float:
        """Record one attempt for `key`. Returns seconds to wait (0.0 = allowed)."""
        now = self._clock()
        with self._lock:
            self._last_seen[key] = now
            if now - self._last_purge >= self.PURGE_INTERVAL_SECONDS:
                self._purge_stale_locked(now)
            locked_until = self._lockout_until.get(key, 0.0)
            if now < locked_until:
                return locked_until - now
            recent = [t for t in self._attempts.get(key, []) if now - t < self.window_seconds]
            recent.append(now)
            if len(recent) > self.rate_limit:
                strikes = self._strikes.get(key, 0) + 1
                self._strikes[key] = strikes
                lockout = min(
                    self.base_lockout_seconds * (2 ** (strikes - 1)), self.lockout_cap_seconds
                )
                self._lockout_until[key] = now + lockout
                self._attempts[key] = []
                return lockout
            self._attempts[key] = recent
            return 0.0

    def _purge_stale_locked(self, now: float) -> None:
        """Drop every key idle past max(window, lockout cap) — its window is empty and
        any lockout has necessarily expired (a lockout never exceeds the cap), so the
        entry carries no live state, only memory (L-4). Caller holds self._lock."""
        retention = max(self.window_seconds, self.lockout_cap_seconds)
        stale = [
            key
            for key, seen in self._last_seen.items()
            if now - seen > retention and self._lockout_until.get(key, 0.0) <= now
        ]
        for key in stale:
            self._last_seen.pop(key, None)
            self._attempts.pop(key, None)
            self._lockout_until.pop(key, None)
            self._strikes.pop(key, None)
        self._last_purge = now

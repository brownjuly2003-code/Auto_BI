"""Unit tests for the sliding-window rate limiter (B-3 login lockout, generalized for the
O-2 per-day LLM-call quota — see auto_bi.api.ratelimit / auto_bi.api.app). A fake clock
makes the sliding window and the exponential backoff deterministic — no real sleeping."""

from auto_bi.api.ratelimit import LoginRateLimiter


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_up_to_the_limit() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for _ in range(LoginRateLimiter.RATE_LIMIT):
        assert limiter.check("1.2.3.4") == 0.0


def test_blocks_past_the_limit_within_the_window() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for _ in range(LoginRateLimiter.RATE_LIMIT):
        limiter.check("1.2.3.4")
    wait = limiter.check("1.2.3.4")
    assert wait > 0


def test_window_slides_so_old_attempts_expire() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for _ in range(LoginRateLimiter.RATE_LIMIT):
        limiter.check("1.2.3.4")
    clock.advance(LoginRateLimiter.WINDOW_SECONDS + 1)
    assert limiter.check("1.2.3.4") == 0.0  # old attempts fell out of the window


def test_keys_are_independent() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for _ in range(LoginRateLimiter.RATE_LIMIT):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4") > 0  # this IP is locked out
    assert limiter.check("5.6.7.8") == 0.0  # a different IP is unaffected


def test_lockout_expires_then_reopens_the_window() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for _ in range(LoginRateLimiter.RATE_LIMIT):
        limiter.check("1.2.3.4")
    wait = limiter.check("1.2.3.4")
    assert wait > 0
    clock.advance(wait + 0.01)
    assert limiter.check("1.2.3.4") == 0.0  # lockout over, fresh window opened


def test_repeated_violations_grow_the_lockout_exponentially() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)

    def _trigger_lockout() -> float:
        for _ in range(LoginRateLimiter.RATE_LIMIT):
            limiter.check("1.2.3.4")
        return limiter.check("1.2.3.4")

    first = _trigger_lockout()
    assert first == LoginRateLimiter.BASE_LOCKOUT_SECONDS
    clock.advance(first + 0.01)

    second = _trigger_lockout()
    assert second == LoginRateLimiter.BASE_LOCKOUT_SECONDS * 2
    clock.advance(second + 0.01)

    third = _trigger_lockout()
    assert third == LoginRateLimiter.BASE_LOCKOUT_SECONDS * 4


def test_lockout_caps_and_never_exceeds_it() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    wait = 0.0
    for _ in range(12):  # enough strikes that 30 * 2**n would blow past the cap
        for _ in range(LoginRateLimiter.RATE_LIMIT):
            limiter.check("1.2.3.4")
        wait = limiter.check("1.2.3.4")
        clock.advance(wait + 0.01)
    assert wait == LoginRateLimiter.LOCKOUT_CAP_SECONDS


# --- generalized construction (O-2: reused at day scale for the session-quota gate) ----


def test_custom_parameters_scale_the_window_and_lockout() -> None:
    # a day-scale quota: 3 calls/day/key instead of the login defaults
    clock = FakeClock()
    limiter = LoginRateLimiter(
        rate_limit=3,
        window_seconds=86400.0,
        base_lockout_seconds=86400.0,
        lockout_cap_seconds=86400.0,
        clock=clock,
    )
    for _ in range(3):
        assert limiter.check("1.2.3.4") == 0.0
    wait = limiter.check("1.2.3.4")
    assert wait == 86400.0  # locked out for the rest of the day, not the login 30s base


def test_custom_parameters_default_to_login_values_when_omitted() -> None:
    # omitting a parameter still falls back to the original login-hardening constants,
    # so the existing login call site (LoginRateLimiter()) is unaffected by generalizing.
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    assert limiter.rate_limit == LoginRateLimiter.RATE_LIMIT
    assert limiter.window_seconds == LoginRateLimiter.WINDOW_SECONDS
    assert limiter.base_lockout_seconds == LoginRateLimiter.BASE_LOCKOUT_SECONDS
    assert limiter.lockout_cap_seconds == LoginRateLimiter.LOCKOUT_CAP_SECONDS


def test_flat_lockout_never_escalates_when_cap_equals_base() -> None:
    # a budget quota (unlike brute-force deterrence) has no reason to grow the lockout on
    # repeated violations: base == cap means every violation gets the same flat wait,
    # however many times the key trips it.
    clock = FakeClock()
    limiter = LoginRateLimiter(
        rate_limit=2,
        window_seconds=1000.0,
        base_lockout_seconds=1000.0,
        lockout_cap_seconds=1000.0,
        clock=clock,
    )
    waits = []
    for _ in range(4):
        for _ in range(2):
            limiter.check("1.2.3.4")
        wait = limiter.check("1.2.3.4")
        waits.append(wait)
        clock.advance(wait + 0.01)
    assert waits == [1000.0, 1000.0, 1000.0, 1000.0]


def test_custom_parameters_keep_keys_independent() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(rate_limit=1, window_seconds=86400.0, clock=clock)
    assert limiter.check("1.2.3.4") == 0.0
    assert limiter.check("1.2.3.4") > 0  # this IP's daily quota is used up
    assert limiter.check("5.6.7.8") == 0.0  # a different IP has its own quota


def test_day_window_rolls_over_after_a_full_day() -> None:
    # a per-day quota (O-2): the window slides on a 24h scale instead of 60s, and the
    # lockout (base == cap) doesn't grow across the reset — the key simply gets its
    # quota back once the rolling day window clears.
    clock = FakeClock()
    limiter = LoginRateLimiter(
        rate_limit=3,
        window_seconds=86400.0,
        base_lockout_seconds=86400.0,
        lockout_cap_seconds=86400.0,
        clock=clock,
    )
    for _ in range(3):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4") == 86400.0  # quota exhausted for today
    clock.advance(86400.0 + 1.0)  # a full day (+ a hair) later
    assert limiter.check("1.2.3.4") == 0.0  # fresh day, fresh quota
    for _ in range(2):
        assert limiter.check("1.2.3.4") == 0.0  # 2 more calls still within today's 3


# --- L-4: stale-key purge (memory hygiene on public exposure) -----------------------


def test_purge_forgets_keys_idle_past_retention() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    # key A earns a strike (escalation state worth remembering)
    for _ in range(LoginRateLimiter.RATE_LIMIT + 1):
        limiter.check("1.2.3.4")
    assert "1.2.3.4" in limiter._strikes
    # idle past max(window, lockout cap); another key's check triggers the purge
    clock.advance(max(LoginRateLimiter.WINDOW_SECONDS, LoginRateLimiter.LOCKOUT_CAP_SECONDS) + 1)
    limiter.check("5.6.7.8")
    assert "1.2.3.4" not in limiter._last_seen
    assert "1.2.3.4" not in limiter._attempts
    assert "1.2.3.4" not in limiter._lockout_until
    assert "1.2.3.4" not in limiter._strikes
    # forgotten = escalation restarts from the base lockout, not from strike #2
    for _ in range(LoginRateLimiter.RATE_LIMIT):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4") == LoginRateLimiter.BASE_LOCKOUT_SECONDS


def test_purge_spares_recent_and_locked_keys() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for _ in range(LoginRateLimiter.RATE_LIMIT + 1):
        limiter.check("1.2.3.4")  # locked out now
    # past the purge interval but well inside the retention window
    clock.advance(LoginRateLimiter.PURGE_INTERVAL_SECONDS + 1)
    limiter.check("5.6.7.8")  # triggers a purge scan
    assert "1.2.3.4" in limiter._strikes  # recent key survives the scan
    # surviving = the escalation continues from strike #2, it does not restart
    for _ in range(LoginRateLimiter.RATE_LIMIT):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4") == LoginRateLimiter.BASE_LOCKOUT_SECONDS * 2


def test_purge_is_amortized_not_per_call() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    limiter.check("1.2.3.4")
    stamp = limiter._last_purge
    clock.advance(1.0)  # under the purge interval: check() must not rescan
    limiter.check("5.6.7.8")
    assert limiter._last_purge == stamp

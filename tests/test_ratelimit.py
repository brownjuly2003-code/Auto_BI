"""Unit tests for the login rate limiter (B-3). A fake clock makes the sliding window
and the exponential backoff deterministic — no real sleeping."""

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

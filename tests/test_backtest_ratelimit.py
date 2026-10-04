"""Phase 1 -- the request limiter never exceeds its budget (fake clock, instant)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

from backtest.data.ratelimit import RateLimiter  # noqa: E402
from backtest_fakes import FakeClock  # noqa: E402


def _run(n, max_per_minute=180, burst=10, gap=0.0):
    clk = FakeClock()
    lim = RateLimiter(max_per_minute, burst, clock=clk.now, sleep=clk.sleep)
    stamps = []
    for _ in range(n):
        lim.acquire()
        stamps.append(clk.t)
        clk.t += gap
    return stamps, clk


def test_no_closed_60s_window_ever_holds_more_than_the_budget():
    stamps, _ = _run(2000)
    j = 0
    for i, t in enumerate(stamps):
        while t - stamps[j] > 60.0:
            j += 1
        assert i - j + 1 <= 180, (i, t)


def test_budget_holds_for_a_small_budget_too():
    stamps, _ = _run(300, max_per_minute=30, burst=5)
    j = 0
    for i, t in enumerate(stamps):
        while t - stamps[j] > 60.0:
            j += 1
        assert i - j + 1 <= 30


def test_burst_is_bounded_then_requests_are_paced():
    stamps, clk = _run(40, max_per_minute=180, burst=10)
    assert stamps[9] == stamps[0]                    # first `burst` go immediately
    assert stamps[-1] > stamps[0]                    # the rest are paced (sleeps happened)
    assert clk.sleeps


def test_sustained_rate_is_at_most_the_configured_rate():
    stamps, _ = _run(1800)                           # 10 minutes' worth
    assert (stamps[-1] - stamps[0]) >= 1800 / 3.0 - 10 / 3.0 - 1.0     # ~3 req/s after the burst


def test_slow_callers_are_never_delayed():
    stamps, clk = _run(20, gap=1.0)                  # 1 req/s is well under budget
    assert clk.sleeps == []


def test_rejects_nonsense_config():
    with pytest.raises(ValueError):
        RateLimiter(0, 1)
    with pytest.raises(ValueError):
        RateLimiter(10, 0)

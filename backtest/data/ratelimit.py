"""Request-rate limiter: a token bucket (smooths bursts) guarded by a strict rolling window.

A bare token bucket of capacity C and rate r can emit C + r*60 requests in a 60 s window, which
would break a hard "at most N a minute" budget. So `acquire()` also enforces a rolling-window
cap: no more than `max_per_minute` acquisitions in ANY 60-second span. Clock and sleep are
injectable so tests run instantly and deterministically.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

WINDOW_S = 60.0
_TOKEN_EPS = 1e-6        # float safety: a refill that lands on 0.99999.. must still make progress
_EPS = 1e-3              # sleep a hair past the window edge so the oldest stamp is strictly older


class RateLimiter:
    def __init__(self, max_per_minute: int = 180, burst: int = 10,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if max_per_minute < 1 or burst < 1:
            raise ValueError("max_per_minute and burst must be >= 1")
        self.max_per_minute = int(max_per_minute)
        self.burst = int(min(burst, max_per_minute))
        self._rate = self.max_per_minute / WINDOW_S          # tokens per second
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(self.burst)
        self._last = clock()
        self._stamps: deque[float] = deque()

    def _refill(self, now: float) -> None:
        self._tokens = min(float(self.burst), self._tokens + (now - self._last) * self._rate)
        self._last = now

    def acquire(self) -> None:
        """Block until one request may be made, then record it."""
        while True:
            now = self._clock()
            self._refill(now)
            while self._stamps and now - self._stamps[0] > WINDOW_S:
                self._stamps.popleft()
            wait = 0.0
            if self._tokens < 1.0:
                wait = max(wait, (1.0 - self._tokens) / self._rate + _TOKEN_EPS)
            if len(self._stamps) >= self.max_per_minute:
                wait = max(wait, WINDOW_S - (now - self._stamps[0]) + _EPS)
            if wait <= 0.0:
                self._tokens -= 1.0
                self._stamps.append(now)
                return
            self._sleep(wait)

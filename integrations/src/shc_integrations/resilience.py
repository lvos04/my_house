"""Retry, circuit breaker and rate limiting shared by all adapters.

No adapter may loop commands without bound: every retry is capped and every
device has a breaker that stops hammering an unreachable device.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .base import AdapterError

T = TypeVar("T")


class CircuitOpen(AdapterError):
    code = "device_offline"


async def retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run `fn` up to `attempts` times with exponential backoff + full jitter."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return await fn()
        except retry_on as exc:  # noqa: PERF203
            last = exc
            if i == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2**i))
            await sleep(random.uniform(0, delay))
    assert last is not None
    raise last


def backoff_delays(base: float = 1.0, maximum: float = 60.0):
    """Infinite generator of jittered backoff delays for reconnect loops."""
    n = 0
    while True:
        d = min(maximum, base * (2**n))
        yield random.uniform(d / 2, d)
        n = min(n + 1, 16)


class CircuitBreaker:
    """closed -> open after `threshold` consecutive failures; half-open after `reset_after` s."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, threshold: int = 5, reset_after: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.threshold = threshold
        self.reset_after = reset_after
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_trial = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return self.CLOSED
        if self._clock() - self._opened_at >= self.reset_after:
            return self.HALF_OPEN
        return self.OPEN

    def allow(self) -> bool:
        st = self.state
        if st == self.CLOSED:
            return True
        if st == self.HALF_OPEN and not self._half_open_trial:
            self._half_open_trial = True
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._half_open_trial = False

    def record_failure(self) -> None:
        self._failures += 1
        if self._half_open_trial or self._failures >= self.threshold:
            self._opened_at = self._clock()
            self._half_open_trial = False

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        if not self.allow():
            raise CircuitOpen("Apparaat tijdelijk niet bereikbaar (circuit breaker open)")
        try:
            result = await fn()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


class RateLimiter:
    """Minimum interval between calls (per key). `wait()` sleeps until allowed."""

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def remaining(self, key: str = "") -> float:
        last = self._last.get(key)
        if last is None:
            return 0.0
        return max(0.0, self.min_interval - (self._clock() - last))

    async def wait(self, key: str = "") -> None:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            rem = self.remaining(key)
            if rem > 0:
                await self._sleep(rem)
            self._last[key] = self._clock()

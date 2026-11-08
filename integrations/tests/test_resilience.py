import pytest

from shc_integrations.resilience import CircuitBreaker, CircuitOpen, RateLimiter, retry


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


async def test_breaker_opens_and_half_opens():
    clock = Clock()
    br = CircuitBreaker(threshold=2, reset_after=10, clock=clock)

    async def boom():
        raise RuntimeError("x")

    async def ok():
        return 1

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await br.call(boom)
    assert br.state == "open"
    with pytest.raises(CircuitOpen):
        await br.call(ok)
    clock.t = 11
    assert br.state == "half_open"
    # failing trial re-opens immediately
    with pytest.raises(RuntimeError):
        await br.call(boom)
    assert br.state == "open"
    clock.t = 22
    assert await br.call(ok) == 1
    assert br.state == "closed"


async def test_half_open_allows_single_trial():
    clock = Clock()
    br = CircuitBreaker(threshold=1, reset_after=5, clock=clock)
    br.record_failure()
    clock.t = 6
    assert br.allow() is True
    assert br.allow() is False


async def test_retry_bounded():
    calls = 0
    sleeps = []

    async def fail():
        nonlocal calls
        calls += 1
        raise ValueError

    async def fake_sleep(s):
        sleeps.append(s)

    with pytest.raises(ValueError):
        await retry(fail, attempts=3, sleep=fake_sleep)
    assert calls == 3 and len(sleeps) == 2


async def test_rate_limiter_waits():
    clock = Clock()
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        clock.t += s

    rl = RateLimiter(2.0, clock=clock, sleep=fake_sleep)
    await rl.wait("a")
    clock.t = 0.5
    await rl.wait("a")
    assert slept == [1.5]
    await rl.wait("b")  # other key unaffected
    assert slept == [1.5]

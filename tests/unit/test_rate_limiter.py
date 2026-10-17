import asyncio

from ishaara.clients.rate_limiter import RateLimiter


class VirtualTime:
    """A clock whose sleep advances it, so tests run instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _limiter(per_minute: int, time: VirtualTime) -> RateLimiter:
    return RateLimiter(per_minute, clock=time.clock, sleep=time.sleep)


async def test_calls_under_the_cap_do_not_wait() -> None:
    time = VirtualTime()
    limiter = _limiter(3, time)

    for _ in range(3):
        await limiter.acquire()

    assert time.sleeps == []


async def test_the_call_over_the_cap_waits_for_the_oldest_to_leave_the_window() -> None:
    time = VirtualTime()
    limiter = _limiter(3, time)
    for _ in range(3):
        await limiter.acquire()
    time.now = 10.0

    await limiter.acquire()

    assert time.sleeps == [50.0]  # the first call (at t=0) leaves the window at t=60
    assert time.now == 60.0


async def test_no_more_than_the_cap_in_any_minute_even_with_many_waiters() -> None:
    time = VirtualTime()
    limiter = _limiter(3, time)
    started: list[float] = []

    async def call() -> None:
        await limiter.acquire()
        started.append(time.now)

    await asyncio.gather(*(call() for _ in range(7)))

    assert len(started) == 7
    for index, moment in enumerate(started):
        in_window = [t for t in started if moment - 60 < t <= moment]
        assert len(in_window) <= 3, (index, started)
    assert time.now >= 120.0  # seven calls at three a minute need at least two full waits


async def test_zero_means_unlimited() -> None:
    time = VirtualTime()
    limiter = _limiter(0, time)

    for _ in range(100):
        await limiter.acquire()

    assert time.sleeps == []

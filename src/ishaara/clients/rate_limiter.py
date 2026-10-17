"""A small client-side rate limiter for APIs with a per-minute request cap."""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

WINDOW_SECONDS = 60.0


class RateLimiter:
    """Allows at most ``requests_per_minute`` acquisitions in any 60-second window.

    Waiters are served in order. A limit of 0 (or less) means unlimited. The limit applies per
    process: with several instances running, each has its own window.

    Args:
        requests_per_minute: The cap, or 0 for none.
        clock: Monotonic clock in seconds.
        sleep: Awaitable sleep, replaceable in tests.
    """

    def __init__(
        self,
        requests_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Store the cap and the time sources."""
        self._requests_per_minute = requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._recent: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request may be made, then count it."""
        if self._requests_per_minute <= 0:
            return
        async with self._lock:
            while True:
                now = self._clock()
                while self._recent and now - self._recent[0] >= WINDOW_SECONDS:
                    self._recent.popleft()
                if len(self._recent) < self._requests_per_minute:
                    self._recent.append(now)
                    return
                await self._sleep(WINDOW_SECONDS - (now - self._recent[0]))

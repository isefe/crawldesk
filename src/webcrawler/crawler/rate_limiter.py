from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.requests_per_second = max(0.1, requests_per_second)
        self._interval = 1.0 / self.requests_per_second
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_time = max(0.0, self._next_allowed - now)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                now = time.monotonic()
            self._next_allowed = max(self._next_allowed, now) + self._interval

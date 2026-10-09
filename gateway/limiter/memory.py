"""In-process token bucket: up to `rpm` tokens per key, refilled at rpm/60 per second.

No lock is needed: nothing in acquire() awaits, and asyncio runs one coroutine at a time.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class MemoryLimiter:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self.now = now
        self.buckets: dict[str, tuple[float, float]] = {}  # key_id -> (tokens, updated_at)

    async def acquire(self, key_id: str, rpm: int) -> tuple[bool, float]:
        now, rate = self.now(), rpm / 60
        tokens, updated = self.buckets.get(key_id, (float(rpm), now))  # new keys start full
        tokens = min(float(rpm), tokens + (now - updated) * rate)
        if tokens >= 1:
            self.buckets[key_id] = (tokens - 1, now)
            return True, 0.0
        self.buckets[key_id] = (tokens, now)
        return False, (1 - tokens) / rate

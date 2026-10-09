"""The Limiter interface. K2 adds a Redis implementation behind the same method."""

from __future__ import annotations

from typing import Protocol


class Limiter(Protocol):
    async def acquire(self, key_id: str, rpm: int) -> tuple[bool, float]:
        """Take one token. Returns (allowed, seconds until a token is available)."""
        ...

"""One monotonic request clock for execution, waits, and critical recording."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, TypeVar

T = TypeVar("T")


class Clock(Protocol):
    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> float:
        return monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class DeadlineExpired(TimeoutError):
    """No further admission, wait, or provider attempt may start."""


@dataclass(frozen=True)
class Deadline:
    clock: Clock
    started: float
    budget_ms: int
    recording_margin_ms: int = 100

    @property
    def ends_at(self) -> float:
        return self.started + self.budget_ms / 1000

    def remaining(self, *, reserve_recording: bool = False) -> float:
        reserve = self.recording_margin_ms / 1000 if reserve_recording else 0
        return max(0.0, self.ends_at - self.clock.now() - reserve)

    def require(self, *, reserve_recording: bool = False) -> float:
        seconds = self.remaining(reserve_recording=reserve_recording)
        if seconds <= 0:
            raise DeadlineExpired()
        return seconds

    async def run(self, start: Callable[[], Awaitable[T]], *, reserve_recording: bool = False) -> T:
        timeout = self.require(reserve_recording=reserve_recording)
        try:
            return await asyncio.wait_for(start(), timeout)
        except TimeoutError as error:
            raise DeadlineExpired() from error

    async def sleep(self, seconds: float) -> None:
        if seconds < 0 or seconds > self.remaining(reserve_recording=True):
            raise DeadlineExpired()
        await self.run(lambda: self.clock.sleep(seconds), reserve_recording=True)

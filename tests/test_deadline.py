import asyncio
from uuid import uuid4

import pytest

from app.api.schemas import GenerateRequest
from app.config import Settings
from app.domain.deadline import Deadline, DeadlineExpired
from app.providers.mock import MockProvider
from app.service import GatewayService
from tests.fakes import MemoryStore


class FakeClock:
    def __init__(self) -> None:
        self.time = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.time

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += seconds


def test_margin_and_one_monotonic_end() -> None:
    clock = FakeClock()
    deadline = Deadline(clock, started=0, budget_ms=1000)
    assert deadline.ends_at == 1.0
    assert deadline.require(reserve_recording=True) == pytest.approx(0.9)
    clock.time = 0.85
    assert deadline.require(reserve_recording=True) == pytest.approx(0.05)
    clock.time = 0.9
    with pytest.raises(DeadlineExpired):
        deadline.require(reserve_recording=True)
    assert deadline.remaining() == pytest.approx(0.1)


async def test_wait_cannot_start_beyond_deadline() -> None:
    clock = FakeClock()
    deadline = Deadline(clock, started=0, budget_ms=1000)
    await deadline.sleep(0.8)
    assert clock.sleeps == [0.8]
    with pytest.raises(DeadlineExpired):
        await deadline.sleep(0.2)
    assert clock.sleeps == [0.8]


async def test_expired_run_never_starts_operation() -> None:
    clock = FakeClock()
    deadline = Deadline(clock, started=0, budget_ms=100)
    started = False

    async def operation() -> None:
        nonlocal started
        started = True

    with pytest.raises(DeadlineExpired):
        await deadline.run(operation, reserve_recording=True)
    assert not started


async def test_real_scheduler_tolerance() -> None:
    from app.domain.deadline import SystemClock

    clock = SystemClock()
    deadline = Deadline(clock, started=clock.now(), budget_ms=150, recording_margin_ms=20)
    with pytest.raises(DeadlineExpired):
        await deadline.run(lambda: asyncio.sleep(0.3), reserve_recording=True)
    # The documented local scheduling tolerance is 50 ms. This is not a claim
    # that cancelling an upstream socket prevents provider billing.
    assert clock.now() <= deadline.ends_at + 0.05


async def test_service_rejects_before_dispatch_when_recording_margin_is_exhausted() -> None:
    clock = FakeClock()
    memory = MemoryStore()
    provider = MockProvider()
    service = GatewayService(Settings(), memory, provider, clock)
    with pytest.raises(DeadlineExpired):
        await service.execute(
            GenerateRequest(input="synthetic", latency_budget_ms=100),
            memory.principal,
            uuid4(),
            started=0,
        )
    assert provider.invocations == 0
    assert memory.records == {}


async def test_service_uses_same_clock_for_execution_and_latency() -> None:
    clock = FakeClock()
    memory = MemoryStore()
    provider = MockProvider()
    service = GatewayService(Settings(), memory, provider, clock)
    response = await service.execute(
        GenerateRequest(input="synthetic", latency_budget_ms=300),
        memory.principal,
        uuid4(),
        started=0,
    )
    assert response.latency_ms == 0
    assert provider.invocations == 1

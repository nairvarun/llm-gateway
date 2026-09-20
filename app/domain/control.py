"""Cross-replica execution controls. Redis server time owns shared leases."""

import re
from collections.abc import Awaitable
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from math import ceil
from typing import Protocol, cast
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.domain.deadline import Deadline, DeadlineExpired
from app.domain.models import StateUnavailable


@dataclass(frozen=True)
class Admission:
    tenant_id: UUID
    provider: str
    lease_id: str
    probe_token: int


@dataclass(frozen=True)
class CircuitSnapshot:
    state: str
    transient_failures: int


class Control(Protocol):
    async def health(self, provider: str) -> Decimal: ...

    async def acquire(self, tenant_id: UUID, provider: str, deadline: Deadline) -> Admission: ...

    async def release(self, lease: Admission, *, transient: bool | None) -> None: ...


class LocalControl:
    """Explicit test-only stand-in. Runtime without Redis fails closed instead."""

    async def health(self, provider: str) -> Decimal:
        return Decimal("1")

    async def acquire(self, tenant_id: UUID, provider: str, deadline: Deadline) -> Admission:
        deadline.require(reserve_recording=True)
        return Admission(tenant_id, provider, uuid4().hex, 0)

    async def release(self, lease: Admission, *, transient: bool | None) -> None:
        return None


class UnavailableControl:
    async def health(self, provider: str) -> Decimal:
        raise StateUnavailable()

    async def acquire(self, tenant_id: UUID, provider: str, deadline: Deadline) -> Admission:
        raise StateUnavailable()

    async def release(self, lease: Admission, *, transient: bool | None) -> None:
        raise StateUnavailable()


_ADMIT = """
local now = redis.call('TIME')
local ts = tonumber(now[1]) + tonumber(now[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ts)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ts)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) or
   redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[4]) then return 0 end
local tr = tonumber(redis.call('GET', KEYS[3]) or '0')
local pr = tonumber(redis.call('GET', KEYS[4]) or '0')
if tr >= tonumber(ARGV[5]) or pr >= tonumber(ARGV[6]) then return -1 end
redis.call('ZADD', KEYS[1], ts + tonumber(ARGV[2]), ARGV[1])
redis.call('ZADD', KEYS[2], ts + tonumber(ARGV[2]), ARGV[1])
redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[2]) + 1))
redis.call('EXPIRE', KEYS[2], math.ceil(tonumber(ARGV[2]) + 1))
redis.call('INCR', KEYS[3]); redis.call('EXPIRE', KEYS[3], 60)
redis.call('INCR', KEYS[4]); redis.call('EXPIRE', KEYS[4], 60)
return 1
"""

_CIRCUIT_ACQUIRE = """
local now = redis.call('TIME')
local ts = tonumber(now[1]) + tonumber(now[2]) / 1000000
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
if state == 'open' then
  if ts < tonumber(redis.call('HGET', KEYS[1], 'until') or '0') then return -1 end
  redis.call('HSET', KEYS[1], 'state', 'half_open')
  state = 'half_open'
end
if state == 'half_open' then
  local probe_until = tonumber(redis.call('HGET', KEYS[1], 'probe_until') or '0')
  if probe_until > ts then return -2 end
  local token = redis.call('INCR', KEYS[2])
  redis.call('HSET', KEYS[1], 'probe_token', token, 'probe_until', ts + tonumber(ARGV[1]))
  return token
end
return 0
"""

_CIRCUIT_REPORT = """
local now = redis.call('TIME')
local ts = tonumber(now[1]) + tonumber(now[2]) / 1000000
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local token = tonumber(ARGV[1])
if token > 0 then
  if state ~= 'half_open' or tonumber(redis.call('HGET', KEYS[1], 'probe_token') or '0') ~= token
  then return 0 end
  redis.call('HDEL', KEYS[1], 'probe_token', 'probe_until')
  if ARGV[2] == 'success' then
    redis.call('DEL', KEYS[1], KEYS[2])
  elseif ARGV[2] == 'transient' then
    redis.call('HSET', KEYS[1], 'state', 'open', 'until', ts + tonumber(ARGV[3]))
  end
  return 1
end
if state ~= 'closed' then return 0 end
if ARGV[2] ~= 'transient' then return 1 end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ts - tonumber(ARGV[4]))
redis.call('ZADD', KEYS[2], ts, ARGV[5])
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]) + 1)
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[6]) then
  redis.call('HSET', KEYS[1], 'state', 'open', 'until', ts + tonumber(ARGV[3]))
end
return 1
"""

_CIRCUIT_ABORT = """
if tonumber(redis.call('HGET', KEYS[1], 'probe_token') or '0') == tonumber(ARGV[1]) then
  redis.call('HDEL', KEYS[1], 'probe_token', 'probe_until')
end
return 1
"""

_CIRCUIT_STATUS = """
local now = redis.call('TIME')
local ts = tonumber(now[1]) + tonumber(now[2]) / 1000000
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
if state == 'open' and ts >= tonumber(redis.call('HGET', KEYS[1], 'until') or '0') then
  state = 'half_open'
end
local count = redis.call('ZCOUNT', KEYS[2], ts - tonumber(ARGV[1]), '+inf')
return {state, tostring(count)}
"""


@dataclass(frozen=True)
class ControlLimits:
    tenant_concurrency: int = 8
    provider_concurrency: int = 16
    tenant_rate_per_minute: int = 60
    provider_rate_per_minute: int = 300
    failure_threshold: int = 5
    failure_window_seconds: int = 60
    open_cooldown_seconds: int = 30
    probe_lease_seconds: int = 30


class RedisControl:
    def __init__(
        self, redis: Redis, limits: ControlLimits | None = None, *, namespace: str = "gateway"
    ) -> None:
        if re.fullmatch(r"[a-z][a-z0-9-]{0,60}", namespace) is None:
            raise ValueError("Invalid control namespace")
        self.redis, self.limits = redis, limits or ControlLimits()
        self.prefix = namespace

    async def _eval(self, script: str, keys: list[str], args: list[str]) -> int:
        result = await cast(Awaitable[object], self.redis.eval(script, len(keys), *keys, *args))
        return int(cast(int | str | bytes, result))

    async def snapshot(self, provider: str) -> CircuitSnapshot:
        try:
            circuit = f"{self.prefix}:circuit:{provider}"
            result = await cast(
                Awaitable[object],
                self.redis.eval(
                    _CIRCUIT_STATUS,
                    2,
                    circuit,
                    f"{circuit}:failures",
                    str(self.limits.failure_window_seconds),
                ),
            )
            state, count = cast(list[bytes], result)
            return CircuitSnapshot(state.decode(), int(count))
        except (OSError, RedisError) as error:
            raise StateUnavailable() from error

    async def health(self, provider: str) -> Decimal:
        state = (await self.snapshot(provider)).state
        return (
            Decimal("0")
            if state == "open"
            else Decimal("0.5")
            if state == "half_open"
            else Decimal("1")
        )

    async def acquire(self, tenant_id: UUID, provider: str, deadline: Deadline) -> Admission:
        lease_id = uuid4().hex
        tenant = f"{self.prefix}:tenant:{tenant_id}"
        domain = f"{self.prefix}:provider:{provider}"
        keys = [f"{tenant}:leases", f"{domain}:leases", f"{tenant}:rate", f"{domain}:rate"]
        while True:
            remaining = deadline.require(reserve_recording=True)
            lease_seconds = max(1, ceil(remaining + 1))
            try:
                result = await deadline.run(
                    partial(
                        self._eval,
                        _ADMIT,
                        keys,
                        [
                            lease_id,
                            str(lease_seconds),
                            str(self.limits.tenant_concurrency),
                            str(self.limits.provider_concurrency),
                            str(self.limits.tenant_rate_per_minute),
                            str(self.limits.provider_rate_per_minute),
                        ],
                    ),
                    reserve_recording=True,
                )
            except (OSError, RedisError) as error:
                raise StateUnavailable() from error
            if result == 1:
                break
            if result == -1:
                raise ControlRejected("rate_limit")
            await deadline.sleep(min(0.05, deadline.require(reserve_recording=True)))
        circuit = f"{self.prefix}:circuit:{provider}"
        try:
            token = await deadline.run(
                partial(
                    self._eval,
                    _CIRCUIT_ACQUIRE,
                    [circuit, f"{circuit}:fence"],
                    [str(self.limits.probe_lease_seconds)],
                ),
                reserve_recording=True,
            )
            if token == -1:
                raise ControlRejected("circuit_open")
            if token == -2:
                raise ControlRejected("circuit_probe_busy")
            return Admission(tenant_id, provider, lease_id, int(token))
        except (OSError, RedisError, ControlRejected, DeadlineExpired) as error:
            try:
                if deadline.remaining() > 0:
                    await deadline.run(lambda: self.redis.zrem(keys[0], lease_id))
                    await deadline.run(lambda: self.redis.zrem(keys[1], lease_id))
            except (OSError, RedisError, DeadlineExpired):
                pass  # The bounded lease expires even if release cannot reach Redis.
            if isinstance(error, (OSError, RedisError)):
                raise StateUnavailable() from error
            raise

    async def release(self, lease: Admission, *, transient: bool | None) -> None:
        circuit = f"{self.prefix}:circuit:{lease.provider}"
        try:
            await self.redis.zrem(f"{self.prefix}:tenant:{lease.tenant_id}:leases", lease.lease_id)
            await self.redis.zrem(f"{self.prefix}:provider:{lease.provider}:leases", lease.lease_id)
            if transient is None and lease.probe_token:
                await self._eval(_CIRCUIT_ABORT, [circuit], [str(lease.probe_token)])
            elif transient is not None:
                await self._eval(
                    _CIRCUIT_REPORT,
                    [circuit, f"{circuit}:failures"],
                    [
                        str(lease.probe_token),
                        "transient" if transient else "success",
                        str(self.limits.open_cooldown_seconds),
                        str(self.limits.failure_window_seconds),
                        uuid4().hex,
                        str(self.limits.failure_threshold),
                    ],
                )
        except (OSError, RedisError) as error:
            raise StateUnavailable() from error


class ControlRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

"""Optional encrypted-entry transport with fenced cross-replica single-flight."""

from collections.abc import Awaitable
from typing import Protocol, cast
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError


class CacheUnavailable(Exception):
    """Optional cache transport failed; critical controls must be checked separately."""


class Cache(Protocol):
    async def read(self, key: str) -> bytes | None: ...

    async def claim(self, key: str, lease_ms: int) -> str | None: ...

    async def publish(self, key: str, token: str, content: bytes, ttl_seconds: int) -> bool: ...

    async def release(self, key: str, token: str) -> None: ...


_CLAIM = """
if redis.call('EXISTS', KEYS[1]) == 1 then return false end
local next = redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], 86400)
local token = tostring(next) .. ':' .. ARGV[2]
if redis.call('SET', KEYS[1], token, 'NX', 'PX', tonumber(ARGV[1])) then
  return token
end
return false
"""

_PUBLISH = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'EX', tonumber(ARGV[3]))
redis.call('DEL', KEYS[1])
return 1
"""

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]) end
return 1
"""


class RedisCache:
    def __init__(self, redis: Redis, *, prefix: str = "gateway:cache") -> None:
        self.redis = redis
        self.prefix = prefix

    def _keys(self, key: str) -> tuple[str, str, str]:
        if not key or any(character not in "0123456789abcdef:-" for character in key):
            raise ValueError("Invalid cache identity")
        base = f"{self.prefix}:{key}"
        return base + ":entry", base + ":lease", base + ":fence"

    async def read(self, key: str) -> bytes | None:
        entry, _, _ = self._keys(key)
        try:
            return await cast(Awaitable[bytes | None], self.redis.get(entry))
        except (OSError, RedisError) as error:
            raise CacheUnavailable() from error

    async def claim(self, key: str, lease_ms: int) -> str | None:
        _, lease, fence = self._keys(key)
        try:
            value = await cast(
                Awaitable[bytes | None],
                self.redis.eval(_CLAIM, 2, lease, fence, str(lease_ms), uuid4().hex),
            )
            return value.decode() if value is not None else None
        except (OSError, RedisError) as error:
            raise CacheUnavailable() from error

    async def publish(self, key: str, token: str, content: bytes, ttl_seconds: int) -> bool:
        entry, lease, _ = self._keys(key)
        try:
            return bool(
                await cast(
                    Awaitable[int],
                    self.redis.eval(
                        _PUBLISH, 2, lease, entry, token, content.decode("utf-8"), str(ttl_seconds)
                    ),
                )
            )
        except (OSError, RedisError) as error:
            raise CacheUnavailable() from error

    async def release(self, key: str, token: str) -> None:
        _, lease, _ = self._keys(key)
        try:
            await cast(Awaitable[int], self.redis.eval(_RELEASE, 1, lease, token))
        except (OSError, RedisError) as error:
            raise CacheUnavailable() from error

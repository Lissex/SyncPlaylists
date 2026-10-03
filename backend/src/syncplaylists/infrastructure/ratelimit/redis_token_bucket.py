import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from redis.asyncio import Redis

from syncplaylists.shared_kernel.domain.errors import PlatformRateLimitedError
from syncplaylists.shared_kernel.domain.value_objects import Platform

# Token bucket с резервированием. Время берётся из Redis (TIME), а не у воркеров —
# часы разных процессов не обязаны совпадать. Токенов может стать меньше нуля: это
# очередь уже выданных разрешений, каждое со своим временем ожидания, поэтому
# конкурентные воркеры не просыпаются одновременно и не штурмуют площадку пачкой.
# Если ждать дольше max_wait — разрешение не выдаётся и не резервируется.
# Возвращает {granted (0/1), wait_ms}.
_LUA: Final = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local max_wait = tonumber(ARGV[3])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local wait = 0
if tokens < 1 then
  wait = math.ceil((1 - tokens) / rate)
end
local granted = 0
if wait <= max_wait then
  tokens = tokens - 1
  granted = 1
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate) + max_wait + 1000)
return {granted, wait}
"""


@dataclass(frozen=True, slots=True)
class TokenBucketLimits:
    capacity: int
    refill_per_second: float
    max_wait_seconds: float

    def __post_init__(self) -> None:
        if self.capacity < 1 or self.refill_per_second <= 0 or self.max_wait_seconds < 0:
            raise ValueError(f"Некорректные параметры token bucket: {self}")


class RedisTokenBucketLimiter:
    """Реализация shared_kernel.PlatformRateLimiter. Площадки без настроенных лимитов
    не ограничиваются (например, фейк)."""

    def __init__(
        self,
        redis: Redis,
        limits: Mapping[Platform, TokenBucketLimits],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._script = redis.register_script(_LUA)
        self._limits = dict(limits)
        self._sleep = sleep

    async def acquire(self, platform: Platform, account_id: UUID) -> None:
        limits = self._limits.get(platform)
        if limits is None:
            return
        granted, wait_ms = await self._script(
            keys=[f"ratelimit:tb:{platform.value}:{account_id}"],
            args=[
                limits.capacity,
                limits.refill_per_second / 1000,
                int(limits.max_wait_seconds * 1000),
            ],
        )
        if not int(granted):
            raise PlatformRateLimitedError(platform, int(wait_ms) / 1000, "token bucket")
        if int(wait_ms) > 0:
            await self._sleep(int(wait_ms) / 1000)

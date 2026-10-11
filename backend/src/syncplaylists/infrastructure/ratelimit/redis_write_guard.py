"""shared_kernel.PlatformWriteGuard на Redis (VK, 4c-5).

Суточный бюджет — счётчик на (площадка, аккаунт, действие, дата). Сутки считаются по
Москве (UTC+3, без перехода на летнее время): пользователю «до следующих суток» — до
полуночи по его часам, а не по UTC. Ступень flood control — счётчик на аккаунт с TTL:
давно не было flood control — снова с первой ступени."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Final
from uuid import UUID

from redis.asyncio import Redis

from syncplaylists.shared_kernel.domain.value_objects import Platform

_DAY_ZONE: Final = timezone(timedelta(hours=3))
# Ключ дня живёт чуть дольше суток — на случай расхождения часов процессов.
_DAY_TTL_SECONDS: Final = 2 * 86400
_FLOOD_TTL_SECONDS: Final = 6 * 3600

# INCR и откат, если бюджет превышен, — атомарно: параллельные задачи не проскочат.
_TAKE: Final = """
local used = redis.call('INCR', KEYS[1])
if used == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
if used > tonumber(ARGV[1]) then
  redis.call('DECR', KEYS[1])
  return 0
end
return 1
"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RedisWriteGuard:
    def __init__(self, redis: Redis, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._redis = redis
        self._clock = clock

    async def take_daily(
        self, platform: Platform, account_id: UUID, action: str, limit: int
    ) -> float | None:
        now = self._clock().astimezone(_DAY_ZONE)
        key = f"writeguard:{platform.value}:{account_id}:{action}:{now:%Y%m%d}"
        taken = await self._redis.eval(_TAKE, 1, key, str(limit), str(_DAY_TTL_SECONDS))  # type: ignore[misc]
        if int(taken) == 1:
            return None
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max((midnight - now).total_seconds(), 1.0)

    async def flood_penalty(
        self, platform: Platform, account_id: UUID, steps: Sequence[float]
    ) -> float:
        key = self._flood_key(platform, account_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, _FLOOD_TTL_SECONDS)
            level, _ = await pipe.execute()
        return float(steps[min(int(level), len(steps)) - 1])

    async def clear_flood(self, platform: Platform, account_id: UUID) -> None:
        await self._redis.delete(self._flood_key(platform, account_id))

    @staticmethod
    def _flood_key(platform: Platform, account_id: UUID) -> str:
        return f"writeguard:{platform.value}:{account_id}:flood"

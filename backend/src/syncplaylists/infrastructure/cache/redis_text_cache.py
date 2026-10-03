from redis.asyncio import Redis


class RedisTextCache:
    """Строковый кэш с TTL поверх Redis (реализация TextCache для CachedSearchGateway)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._redis.set(key, value.encode("utf-8"), ex=max(ttl_seconds, 1))

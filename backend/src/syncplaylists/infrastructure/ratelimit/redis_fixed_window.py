from redis.asyncio import Redis


class RedisFixedWindowLimiter:
    """Фиксированное окно: INCR счётчика + EXPIRE NX при первом попадании. Не точный
    sliding window (на стыке окон можно успеть до ~2×attempts), но для защиты входа
    от перебора этого достаточно, и это одна транзакция Redis."""

    def __init__(self, redis: Redis, attempts: int, window_seconds: int) -> None:
        self._redis = redis
        self._attempts = attempts
        self._window = window_seconds

    async def hit(self, key: str) -> int | None:
        redis_key = f"ratelimit:{key}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, self._window, nx=True)
            pipe.ttl(redis_key)
            count, _, ttl = await pipe.execute()
        if int(count) > self._attempts:
            return max(int(ttl), 1)
        return None

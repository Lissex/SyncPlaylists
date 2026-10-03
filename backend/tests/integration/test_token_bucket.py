"""Token bucket на настоящем Redis (testcontainers): атомарность Lua-скрипта и время
из Redis TIME тут и проверяются — на фейке их не проверить."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from syncplaylists.infrastructure.ratelimit.redis_token_bucket import (
    RedisTokenBucketLimiter,
    TokenBucketLimits,
)
from syncplaylists.shared_kernel.domain.errors import PlatformRateLimitedError
from syncplaylists.shared_kernel.domain.value_objects import Platform


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


class _RecordingSleep:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def _limiter(
    redis: Redis, sleep: _RecordingSleep, capacity: int = 3, rate: float = 1.0, max_wait: float = 5
) -> RedisTokenBucketLimiter:
    return RedisTokenBucketLimiter(
        redis,
        {Platform.YANDEX: TokenBucketLimits(capacity, rate, max_wait)},
        sleep=sleep,
    )


async def test_burst_up_to_capacity_without_waiting(redis: Redis) -> None:
    sleep = _RecordingSleep()
    limiter = _limiter(redis, sleep, capacity=3)
    account = uuid4()

    for _ in range(3):
        await limiter.acquire(Platform.YANDEX, account)

    assert sleep.waits == []


async def test_over_capacity_waits_for_refill_and_queues_waiters(redis: Redis) -> None:
    sleep = _RecordingSleep()
    limiter = _limiter(redis, sleep, capacity=2, rate=2.0)  # токен раз в 0.5 с
    account = uuid4()

    for _ in range(4):
        await limiter.acquire(Platform.YANDEX, account)

    # Третий ждёт ~0.5 с, четвёртый — ~1 с: ожидания не одинаковые, воркеры не
    # просыпаются пачкой.
    assert len(sleep.waits) == 2
    assert 0.3 <= sleep.waits[0] <= 0.5
    assert 0.8 <= sleep.waits[1] <= 1.0


async def test_wait_longer_than_max_is_rate_limited_and_not_reserved(redis: Redis) -> None:
    sleep = _RecordingSleep()
    limiter = _limiter(redis, sleep, capacity=1, rate=0.1, max_wait=1)  # токен раз в 10 с
    account = uuid4()
    await limiter.acquire(Platform.YANDEX, account)

    with pytest.raises(PlatformRateLimitedError) as caught:
        await limiter.acquire(Platform.YANDEX, account)
    assert 9 <= caught.value.retry_after_seconds <= 10
    # Отказ не съел токен в долг — следующая попытка видит то же ожидание, а не больше.
    with pytest.raises(PlatformRateLimitedError) as again:
        await limiter.acquire(Platform.YANDEX, account)
    assert again.value.retry_after_seconds <= caught.value.retry_after_seconds


async def test_accounts_have_separate_buckets(redis: Redis) -> None:
    sleep = _RecordingSleep()
    limiter = _limiter(redis, sleep, capacity=1, rate=0.1, max_wait=0)

    await limiter.acquire(Platform.YANDEX, uuid4())
    await limiter.acquire(Platform.YANDEX, uuid4())

    assert sleep.waits == []


async def test_platform_without_limits_is_not_limited(redis: Redis) -> None:
    sleep = _RecordingSleep()
    limiter = _limiter(redis, sleep, capacity=1, rate=0.1, max_wait=0)
    account = uuid4()

    for _ in range(5):
        await limiter.acquire(Platform.SPOTIFY, account)

    assert await redis.keys("ratelimit:tb:spotify:*") == []


async def test_bucket_key_expires(redis: Redis) -> None:
    limiter = _limiter(redis, _RecordingSleep(), capacity=2, rate=1.0, max_wait=1)
    account = uuid4()

    await limiter.acquire(Platform.YANDEX, account)

    ttl = await redis.pttl(f"ratelimit:tb:yandex:{account}")
    assert 0 < ttl <= 2000 + 1000 + 1000

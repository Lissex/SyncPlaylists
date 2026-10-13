"""Лок распознавания и общий темп распознавателей на настоящем Redis (testcontainers)."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from syncplaylists.infrastructure.ratelimit.redis_token_bucket import (
    RedisServiceThrottle,
    TokenBucketLimits,
)
from syncplaylists.modules.recognition.infrastructure.lock import RedisRecognitionLock
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform

_REF = ExternalTrackRef(Platform.SOUNDCLOUD, "1057094605")


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


async def test_lock_is_exclusive_and_released(redis: Redis) -> None:
    lock = RedisRecognitionLock(redis, ttl_seconds=60)

    async with lock.hold(_REF) as first:
        async with lock.hold(_REF) as second:
            assert first
            assert not second
        # Второй (не получивший) лок не снял чужой.
        async with lock.hold(_REF) as third:
            assert not third

    async with lock.hold(_REF) as after_release:
        assert after_release


async def test_lock_does_not_release_someone_elses_after_expiry(redis: Redis) -> None:
    lock = RedisRecognitionLock(redis, ttl_seconds=1)
    key = f"recognition:lock:soundcloud:{_REF.external_id}"

    async with lock.hold(_REF) as acquired:
        assert acquired
        # Лок истёк (воркер завис дольше TTL), его взял другой.
        await redis.delete(key)
        await redis.set(key, "other-worker")

    assert await redis.get(key) == b"other-worker"


async def test_lock_released_on_error(redis: Redis) -> None:
    lock = RedisRecognitionLock(redis, ttl_seconds=60)

    with pytest.raises(RuntimeError):
        async with lock.hold(_REF):
            raise RuntimeError("сбой распознавания")

    async with lock.hold(_REF) as acquired:
        assert acquired


async def test_throttle_paces_whole_server(redis: Redis) -> None:
    sleep = _RecordingSleep()
    # 1 запрос в 5 с, ждать не дольше 6 с.
    throttle = RedisServiceThrottle(redis, {"shazam": TokenBucketLimits(1, 0.2, 6)}, sleep=sleep)
    other_process = RedisServiceThrottle(
        redis, {"shazam": TokenBucketLimits(1, 0.2, 6)}, sleep=sleep
    )

    assert await throttle.acquire("shazam") is None
    assert await other_process.acquire("shazam") is None  # ждал ~5 с
    assert sleep.waits == [pytest.approx(5, abs=0.2)]
    # Третий ждал бы ~10 с — дольше допустимого: не идёт, а откладывается.
    wait = await throttle.acquire("shazam")
    assert wait == pytest.approx(10, abs=0.3)


async def test_penalize_pauses_everyone(redis: Redis) -> None:
    throttle = RedisServiceThrottle(redis, {"shazam": TokenBucketLimits(1, 0.2, 10)})

    await throttle.penalize("shazam", 600)
    wait = await throttle.acquire("shazam")

    assert wait == pytest.approx(600, abs=1)


async def test_provider_without_limits_is_not_throttled(redis: Redis) -> None:
    throttle = RedisServiceThrottle(redis, {})

    results = await asyncio.gather(*(throttle.acquire("acrcloud") for _ in range(5)))

    assert results == [None] * 5

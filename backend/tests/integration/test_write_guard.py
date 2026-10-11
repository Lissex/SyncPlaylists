"""Бережная запись VK (4c-5) на настоящем Redis: суточный бюджет атомарен и не уходит в
минус, сутки — по Москве, ступени flood control растут и сбрасываются."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from syncplaylists.infrastructure.ratelimit.redis_write_guard import RedisWriteGuard
from syncplaylists.shared_kernel.domain.value_objects import Platform

# 20:30 UTC = 23:30 по Москве: до следующих суток полчаса.
_LATE_EVENING = datetime(2026, 10, 8, 20, 30, tzinfo=UTC)


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


async def test_daily_budget_is_atomic_and_tells_time_to_next_day(redis: Redis) -> None:
    guard = RedisWriteGuard(redis, clock=lambda: _LATE_EVENING)
    account = uuid4()

    results = await asyncio.gather(
        *(guard.take_daily(Platform.VK, account, "library_add", 3) for _ in range(5))
    )

    assert results.count(None) == 3
    waits = [r for r in results if r is not None]
    assert waits == [1800.0, 1800.0]
    # Отказ не тратит бюджет: после полуночи по Москве — снова 3.
    next_day = RedisWriteGuard(redis, clock=lambda: datetime(2026, 10, 8, 21, 1, tzinfo=UTC))
    assert await next_day.take_daily(Platform.VK, account, "library_add", 3) is None


async def test_budget_is_per_account_and_action(redis: Redis) -> None:
    guard = RedisWriteGuard(redis, clock=lambda: _LATE_EVENING)
    first, second = uuid4(), uuid4()
    assert await guard.take_daily(Platform.VK, first, "library_add", 1) is None
    assert await guard.take_daily(Platform.VK, first, "library_add", 1) is not None
    assert await guard.take_daily(Platform.VK, second, "library_add", 1) is None
    assert await guard.take_daily(Platform.VK, first, "other", 1) is None


async def test_flood_steps_grow_repeat_last_and_reset(redis: Redis) -> None:
    guard = RedisWriteGuard(redis)
    account = uuid4()
    steps = (300.0, 900.0, 3600.0)

    got = [await guard.flood_penalty(Platform.VK, account, steps) for _ in range(4)]
    assert got == [300.0, 900.0, 3600.0, 3600.0]

    await guard.clear_flood(Platform.VK, account)
    assert await guard.flood_penalty(Platform.VK, account, steps) == 300.0

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from syncplaylists.infrastructure.ratelimit.redis_fixed_window import RedisFixedWindowLimiter
from syncplaylists.modules.accounts.application.ports import PendingOAuth
from syncplaylists.modules.accounts.infrastructure.oauth import RedisOAuthStateStore
from syncplaylists.modules.identity.infrastructure.sessions import RedisSessionStore
from syncplaylists.shared_kernel.domain.value_objects import Platform


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(redis_url)
    try:
        yield client
    finally:
        await client.aclose()


async def test_session_issue_and_resolve(redis: Redis) -> None:
    store = RedisSessionStore(redis, ttl_seconds=600)
    user_id = uuid4()

    token = await store.issue(user_id)

    assert await store.resolve(token) == user_id
    assert await store.resolve("unknown-token") is None


async def test_session_key_has_ttl_and_no_raw_token(redis: Redis) -> None:
    store = RedisSessionStore(redis, ttl_seconds=600)
    user_id = uuid4()

    token = await store.issue(user_id)

    keys = [k.decode() for k in await redis.keys("session:*")]
    assert all(token not in key for key in keys)
    session_keys = [k for k in keys if (await redis.get(k)) == str(user_id).encode()]
    assert len(session_keys) == 1
    assert 0 < await redis.ttl(session_keys[0]) <= 600
    assert 0 < await redis.ttl(f"user_sessions:{user_id}") <= 600


async def test_session_revoke(redis: Redis) -> None:
    store = RedisSessionStore(redis, ttl_seconds=600)
    user_id = uuid4()
    first = await store.issue(user_id)
    second = await store.issue(user_id)

    await store.revoke(first)

    assert await store.resolve(first) is None
    assert await store.resolve(second) == user_id


async def test_session_revoke_all_kills_only_users_sessions(redis: Redis) -> None:
    store = RedisSessionStore(redis, ttl_seconds=600)
    alice, bob = uuid4(), uuid4()
    alice_tokens = [await store.issue(alice) for _ in range(3)]
    bob_token = await store.issue(bob)

    await store.revoke_all(alice)

    assert [await store.resolve(t) for t in alice_tokens] == [None, None, None]
    assert await store.resolve(bob_token) == bob
    assert await redis.exists(f"user_sessions:{alice}") == 0


async def test_fixed_window_limiter(redis: Redis) -> None:
    limiter = RedisFixedWindowLimiter(redis, attempts=5, window_seconds=60)
    key = f"auth:login:1.2.3.4:{uuid4().hex}"

    results = [await limiter.hit(key) for _ in range(5)]
    sixth = await limiter.hit(key)

    assert results == [None] * 5
    assert sixth is not None
    assert 0 < sixth <= 60
    assert await limiter.hit(f"auth:login:1.2.3.4:{uuid4().hex}") is None  # другой ключ


async def test_oauth_state_is_single_use(redis: Redis) -> None:
    store = RedisOAuthStateStore(redis)
    pending = PendingOAuth(user_id=uuid4(), platform=Platform.SPOTIFY, code_verifier="v" * 64)
    state = uuid4().hex

    await store.save(state, pending, ttl_seconds=600)

    assert await store.pop(state) == pending
    assert await store.pop(state) is None

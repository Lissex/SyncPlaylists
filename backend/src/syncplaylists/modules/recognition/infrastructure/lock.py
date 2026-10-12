from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final
from uuid import uuid4

from redis.asyncio import Redis

from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef

# Снять лок, только если он всё ещё наш: истёк по TTL и его взял другой — не трогаем.
_RELEASE_LUA: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisRecognitionLock:
    """Реализация recognition.RecognitionLock: SET NX с TTL на время распознавания
    (TTL — страховка от упавшего воркера, больше самого долгого распознавания)."""

    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._release = redis.register_script(_RELEASE_LUA)
        self._ttl = ttl_seconds

    def hold(self, source_ref: ExternalTrackRef) -> AbstractAsyncContextManager[bool]:
        return self._hold(f"recognition:lock:{source_ref.platform.value}:{source_ref.external_id}")

    @asynccontextmanager
    async def _hold(self, key: str) -> AsyncIterator[bool]:
        token = uuid4().hex
        acquired = bool(await self._redis.set(key, token, ex=self._ttl, nx=True))
        try:
            yield acquired
        finally:
            if acquired:
                await self._release(keys=[key], args=[token])

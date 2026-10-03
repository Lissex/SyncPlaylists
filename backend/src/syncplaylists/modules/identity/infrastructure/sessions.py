import hashlib
import secrets
from uuid import UUID

from redis.asyncio import Redis

_TOKEN_BYTES = 32


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _as_str(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


class RedisSessionStore:
    """Сессии в Redis: `session:<sha256(token)>` → user_id (с TTL); индекс сессий
    пользователя — SET `user_sessions:<user_id>` из тех же хешей (для «выйти со всех
    устройств»). В Redis лежит хеш, а не сам токен — дамп Redis не даёт готовых cookie.
    TTL фиксированный, без продления при активности (долг — ARCHITECTURE.md, 11b)."""

    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _session_key(digest: str) -> str:
        return f"session:{digest}"

    @staticmethod
    def _index_key(user_id: UUID) -> str:
        return f"user_sessions:{user_id}"

    async def issue(self, user_id: UUID) -> str:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        digest = _digest(token)
        index_key = self._index_key(user_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(self._session_key(digest), str(user_id), ex=self._ttl)
            pipe.sadd(index_key, digest)
            # Индекс живёт не меньше самой свежей сессии; протухшие хеши в нём безвредны —
            # revoke_all просто удалит уже несуществующие ключи.
            pipe.expire(index_key, self._ttl)
            await pipe.execute()
        return token

    async def resolve(self, token: str) -> UUID | None:
        raw = await self._redis.get(self._session_key(_digest(token)))
        if raw is None:
            return None
        try:
            return UUID(_as_str(raw))
        except ValueError:
            return None

    async def revoke(self, token: str) -> None:
        digest = _digest(token)
        user_id = await self.resolve(token)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(self._session_key(digest))
            if user_id is not None:
                pipe.srem(self._index_key(user_id), digest)
            await pipe.execute()

    async def revoke_all(self, user_id: UUID) -> None:
        index_key = self._index_key(user_id)
        members = await self._redis.smembers(index_key)  # type: ignore[misc]
        async with self._redis.pipeline(transaction=True) as pipe:
            for member in members:
                pipe.delete(self._session_key(_as_str(member)))
            pipe.delete(index_key)
            await pipe.execute()

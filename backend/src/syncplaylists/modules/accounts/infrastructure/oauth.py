import json
from collections.abc import Iterable
from uuid import UUID

from redis.asyncio import Redis

from syncplaylists.modules.accounts.application.ports import OAuthProvider, PendingOAuth
from syncplaylists.shared_kernel.domain.value_objects import Platform


class RedisOAuthStateStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _key(state: str) -> str:
        return f"oauth_state:{state}"

    async def save(self, state: str, pending: PendingOAuth, ttl_seconds: int) -> None:
        payload = json.dumps(
            {
                "user_id": str(pending.user_id),
                "platform": pending.platform.value,
                "code_verifier": pending.code_verifier,
            }
        )
        await self._redis.set(self._key(state), payload, ex=ttl_seconds)

    async def pop(self, state: str) -> PendingOAuth | None:
        # GETDEL атомарен — один и тот же state нельзя использовать дважды.
        raw = await self._redis.getdel(self._key(state))
        if raw is None:
            return None
        data = json.loads(raw)
        return PendingOAuth(
            user_id=UUID(data["user_id"]),
            platform=Platform(data["platform"]),
            code_verifier=data["code_verifier"],
        )


class DictOAuthProviderRegistry:
    def __init__(self, providers: Iterable[OAuthProvider]) -> None:
        self._providers = {provider.platform: provider for provider in providers}

    def get(self, platform: Platform) -> OAuthProvider | None:
        return self._providers.get(platform)

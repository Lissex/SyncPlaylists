import json
from typing import Final
from uuid import UUID

from redis.asyncio import Redis

from syncplaylists.modules.extension.application.ports import PendingPairing

_PAIRING: Final = "ext:pair:"  # ext:pair:<sha256(pairing_id)> → JSON PendingPairing
_CODE: Final = "ext:paircode:"  # ext:paircode:<sha256(code)> → sha256(pairing_id)

# Подтверждение: код одноразовый (GETDEL), привязка получает user_id с прежним TTL.
_CONFIRM_LUA: Final = """
local pairing_key = redis.call('GETDEL', KEYS[1])
if not pairing_key then return 0 end
local raw = redis.call('GET', ARGV[2] .. pairing_key)
if not raw then return 0 end
local data = cjson.decode(raw)
if data['user_id'] ~= cjson.null and data['user_id'] ~= nil then return 0 end
data['user_id'] = ARGV[1]
redis.call('SET', ARGV[2] .. pairing_key, cjson.encode(data), 'KEEPTTL')
return 1
"""


def _encode(pairing: PendingPairing) -> str:
    return json.dumps(
        {
            "device_name": pairing.device_name,
            "browser": pairing.browser,
            "version": pairing.version,
            "user_id": str(pairing.user_id) if pairing.user_id else None,
        }
    )


def _decode(raw: bytes | str) -> PendingPairing:
    data = json.loads(raw)
    user_id = data.get("user_id")
    return PendingPairing(
        device_name=data["device_name"],
        browser=data["browser"],
        version=data["version"],
        user_id=UUID(user_id) if user_id else None,
    )


class RedisPairingStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create(
        self, pairing_id_hash: str, code_hash: str, pairing: PendingPairing, ttl_seconds: int
    ) -> bool:
        if not await self._redis.set(_CODE + code_hash, pairing_id_hash, ex=ttl_seconds, nx=True):
            return False
        await self._redis.set(_PAIRING + pairing_id_hash, _encode(pairing), ex=ttl_seconds)
        return True

    async def confirm(self, code_hash: str, user_id: UUID) -> bool:
        result = await self._redis.eval(  # type: ignore[misc]
            _CONFIRM_LUA, 1, _CODE + code_hash, str(user_id), _PAIRING
        )
        return bool(result)

    async def get(self, pairing_id_hash: str) -> PendingPairing | None:
        raw = await self._redis.get(_PAIRING + pairing_id_hash)
        return _decode(raw) if raw is not None else None

    async def take(self, pairing_id_hash: str) -> PendingPairing | None:
        raw = await self._redis.getdel(_PAIRING + pairing_id_hash)
        return _decode(raw) if raw is not None else None

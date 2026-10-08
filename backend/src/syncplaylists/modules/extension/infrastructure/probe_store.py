import json
from typing import Final
from uuid import UUID

from redis.asyncio import Redis

from syncplaylists.modules.extension.application.ports import ProbeRecord

_PROBE: Final = "ext:probe:"  # ext:probe:<probe_id> → JSON ProbeRecord (10 минут)


class RedisProbeStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def save(self, probe_id: str, record: ProbeRecord, ttl_seconds: int) -> None:
        payload = {
            "user_id": str(record.user_id),
            "device_id": str(record.device_id),
            "status": record.status,
            "data": dict(record.data) if record.data is not None else None,
            "error": record.error,
        }
        await self._redis.set(_PROBE + probe_id, json.dumps(payload), ex=ttl_seconds)

    async def get(self, probe_id: str) -> ProbeRecord | None:
        raw = await self._redis.get(_PROBE + probe_id)
        if raw is None:
            return None
        payload = json.loads(raw)
        return ProbeRecord(
            user_id=UUID(payload["user_id"]),
            device_id=UUID(payload["device_id"]),
            status=payload["status"],
            data=payload["data"],
            error=payload["error"],
        )

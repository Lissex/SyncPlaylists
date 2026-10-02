import json
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from redis.asyncio import Redis


def _json_default(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Объект типа {type(value)} не сериализуется в JSON: {value!r}")


class RedisEventPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, topic: str, payload: Mapping[str, Any]) -> None:
        await self._redis.publish(topic, json.dumps(payload, default=_json_default))

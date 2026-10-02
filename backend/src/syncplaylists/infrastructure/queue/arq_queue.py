from typing import Any

from arq.connections import ArqRedis


class ArqTaskQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue(self, task_name: str, *args: Any, **kwargs: Any) -> None:
        await self._redis.enqueue_job(task_name, *args, **kwargs)

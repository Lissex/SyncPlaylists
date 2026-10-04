from datetime import datetime
from typing import Any

from arq.connections import ArqRedis


class ArqTaskQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue(self, task_name: str, *args: Any, **kwargs: Any) -> None:
        await self._redis.enqueue_job(task_name, *args, **kwargs)

    async def enqueue_at(
        self, task_name: str, when: datetime, *args: Any, dedupe_key: str | None = None
    ) -> None:
        # _job_id: ARQ не ставит вторую задачу с тем же id, пока первая не выполнена.
        await self._redis.enqueue_job(task_name, *args, _job_id=dedupe_key, _defer_until=when)

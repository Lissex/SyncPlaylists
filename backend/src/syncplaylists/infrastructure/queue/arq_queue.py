from collections.abc import Mapping
from datetime import datetime
from typing import Any

from arq.connections import ArqRedis

from syncplaylists.shared_kernel.application.ports import TaskLane


class ArqTaskQueue:
    def __init__(self, redis: ArqRedis, queue_names: Mapping[TaskLane, str]) -> None:
        self._redis = redis
        # Каждая полоса — своя очередь ARQ (её слушает свой воркер, bootstrap/worker.py).
        self._queue_names = queue_names

    async def enqueue(self, task_name: str, *args: Any, lane: TaskLane = TaskLane.DEFAULT) -> None:
        await self._redis.enqueue_job(task_name, *args, _queue_name=self._queue_names[lane])

    async def enqueue_at(
        self,
        task_name: str,
        when: datetime,
        *args: Any,
        dedupe_key: str | None = None,
        lane: TaskLane = TaskLane.DEFAULT,
    ) -> None:
        # _job_id: ARQ не ставит вторую задачу с тем же id, пока первая не выполнена.
        await self._redis.enqueue_job(
            task_name,
            *args,
            _job_id=dedupe_key,
            _defer_until=when,
            _queue_name=self._queue_names[lane],
        )

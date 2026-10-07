"""ArqTaskQueue кладёт задачу в очередь ARQ своей полосы (TaskLane)."""

from datetime import UTC, datetime
from typing import Any, cast

from arq.connections import ArqRedis

from syncplaylists.infrastructure.queue.arq_queue import ArqTaskQueue
from syncplaylists.shared_kernel.application.ports import TaskLane


class _RecordingRedis:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((function, args, kwargs))


def _queue() -> tuple[ArqTaskQueue, _RecordingRedis]:
    redis = _RecordingRedis()
    names = {TaskLane.DEFAULT: "arq:queue", TaskLane.CLIENT: "arq:extension"}
    return ArqTaskQueue(cast(ArqRedis, redis), names), redis


async def test_enqueue_routes_by_lane() -> None:
    queue, redis = _queue()

    await queue.enqueue("run_match", "t", 1)
    await queue.enqueue("run_match", "t", 2, lane=TaskLane.CLIENT)

    assert redis.jobs == [
        ("run_match", ("t", 1), {"_queue_name": "arq:queue"}),
        ("run_match", ("t", 2), {"_queue_name": "arq:extension"}),
    ]


async def test_enqueue_at_routes_by_lane_and_keeps_dedupe() -> None:
    queue, redis = _queue()
    when = datetime(2026, 10, 7, tzinfo=UTC)

    await queue.enqueue_at("resume_transfer", when, "t", dedupe_key="k", lane=TaskLane.CLIENT)

    assert redis.jobs == [
        (
            "resume_transfer",
            ("t",),
            {"_job_id": "k", "_defer_until": when, "_queue_name": "arq:extension"},
        )
    ]

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID

from syncplaylists.modules.transfers.application.ports import ClientPause, ProgressSample
from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.value_objects import (
    TransferItemStatus,
    TransferProgress,
    TransferStatus,
    destination_platform,
    source_platform,
)
from syncplaylists.shared_kernel.application.ports import TaskLane
from syncplaylists.shared_kernel.domain.base import AggregateRoot
from syncplaylists.shared_kernel.domain.value_objects import Platform


class FakeEventPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, payload: Mapping[str, Any]) -> None:
        self.published.append((topic, dict(payload)))


class FakeTaskQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[Any, ...]]] = []
        # Полоса каждой постановки (enqueue и enqueue_at) — по порядку.
        self.lanes: list[tuple[str, TaskLane]] = []
        # Отложенные: (задача, когда, аргументы); один ключ dedupe — одна задача.
        self.scheduled: list[tuple[str, datetime, tuple[Any, ...]]] = []
        self._dedupe_keys: set[str] = set()

    async def enqueue(self, task_name: str, *args: Any, lane: TaskLane = TaskLane.DEFAULT) -> None:
        self.enqueued.append((task_name, args))
        self.lanes.append((task_name, lane))

    async def enqueue_at(
        self,
        task_name: str,
        when: datetime,
        *args: Any,
        dedupe_key: str | None = None,
        lane: TaskLane = TaskLane.DEFAULT,
    ) -> None:
        self.lanes.append((task_name, lane))
        if dedupe_key is not None:
            if dedupe_key in self._dedupe_keys:
                return
            self._dedupe_keys.add(dedupe_key)
        self.scheduled.append((task_name, when, args))


class RecordingPause:
    """PauseTransferForQuotaUseCase для тестов задач: только запоминает вызовы."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, float]] = []

    async def execute(self, transfer_id: UUID, retry_after_seconds: float) -> None:
        self.calls.append((transfer_id, retry_after_seconds))


class FakeTransferRepository:
    def __init__(self) -> None:
        self._storage: dict[UUID, Transfer] = {}
        self._updated_at: dict[UUID, datetime] = {}
        self.save_calls = 0
        self.item_outcome_calls = 0
        # Когда items вышли из PENDING — как processed_at в transfer_items (для ETA).
        self.processed_at: dict[UUID, list[datetime]] = {}

    async def get(self, transfer_id: UUID) -> Transfer | None:
        return self._storage.get(transfer_id)

    async def progress_sample(self, transfer_id: UUID, recent: int) -> ProgressSample | None:
        stored = self._storage.get(transfer_id)
        if stored is None:
            return None
        times = sorted(self.processed_at.get(transfer_id, []), reverse=True)[:recent]
        return ProgressSample(
            user_id=stored.user_id,
            status=stored.status,
            progress=TransferProgress.from_statuses(i.status for i in stored.items),
            recent_processed_at=tuple(times),
            resume_at=stored.resume_at,
            pause_reason=stored.pause_reason,
        )

    async def get_for_update(self, transfer_id: UUID) -> Transfer | None:
        # В тестах однопоточно — реальный лок проверяется интеграционными тестами
        # на настоящем Postgres (SqlTransferRepository).
        return self._storage.get(transfer_id)

    async def save(self, transfer: Transfer) -> None:
        self.save_calls += 1
        self._storage[transfer.id] = transfer
        self._updated_at[transfer.id] = datetime.now(UTC)

    async def get_header(self, transfer_id: UUID) -> Transfer | None:
        # Отдельный объект без items, как у SqlTransferRepository: изменения в нём не
        # видны хранилищу, пока не прошли через save_item_outcome/transition_status.
        stored = self._storage.get(transfer_id)
        if stored is None:
            return None
        return dataclasses.replace(stored, items=[], _domain_events=[])

    async def get_item(self, transfer_id: UUID, position: int) -> TransferItem | None:
        stored = self._storage.get(transfer_id)
        item = next((i for i in stored.items if i.position == position), None) if stored else None
        return dataclasses.replace(item) if item is not None else None

    async def save_item_outcome(self, item: TransferItem) -> TransferProgress | None:
        stored = self._storage[item.transfer_id]
        target = next(i for i in stored.items if i.position == item.position)
        if target.status is not TransferItemStatus.PENDING:
            return None
        target.status = item.status
        target.match = item.match
        target.candidates = item.candidates
        self.processed_at.setdefault(item.transfer_id, []).append(datetime.now(UTC))
        self.item_outcome_calls += 1
        return TransferProgress.from_statuses(i.status for i in stored.items)

    async def transition_status(
        self, transfer_id: UUID, from_status: TransferStatus, to_status: TransferStatus
    ) -> bool:
        stored = self._storage[transfer_id]
        if stored.status is not from_status:
            return False
        stored.status = to_status
        return True

    async def pause_for_quota(self, transfer_id: UUID, resume_at: datetime) -> datetime | None:
        stored = self._storage[transfer_id]
        pausable = (TransferStatus.QUEUED, TransferStatus.RUNNING, TransferStatus.WRITING)
        if stored.status is TransferStatus.PAUSED_QUOTA:
            assert stored.resume_at is not None
            stored.resume_at = max(stored.resume_at, resume_at)
        elif stored.status in pausable:
            stored.paused_from, stored.status = stored.status, TransferStatus.PAUSED_QUOTA
            stored.resume_at = resume_at
        else:
            return None
        return stored.resume_at

    async def resume_from_quota(self, transfer_id: UUID, now: datetime) -> TransferStatus | None:
        stored = self._storage[transfer_id]
        if stored.status is not TransferStatus.PAUSED_QUOTA:
            return None
        assert stored.resume_at is not None
        assert stored.paused_from is not None
        if stored.resume_at > now:
            return None
        stored.status, stored.paused_from, stored.resume_at = stored.paused_from, None, None
        return stored.status

    async def pause_for_client(self, transfer_id: UUID, reason: str) -> ClientPause | None:
        stored = self._storage[transfer_id]
        pausable = (TransferStatus.QUEUED, TransferStatus.RUNNING, TransferStatus.WRITING)
        if stored.status is TransferStatus.PAUSED_CLIENT:
            previous = stored.pause_reason
        elif stored.status in pausable:
            previous = None
            stored.paused_from, stored.status = stored.status, TransferStatus.PAUSED_CLIENT
        else:
            return None
        stored.pause_reason = reason
        return ClientPause(previous)

    async def resume_from_client(self, transfer_id: UUID) -> TransferStatus | None:
        stored = self._storage[transfer_id]
        if stored.status is not TransferStatus.PAUSED_CLIENT:
            return None
        assert stored.paused_from is not None
        stored.status, stored.paused_from, stored.pause_reason = stored.paused_from, None, None
        return stored.status

    async def find_client_paused(self, user_id: UUID, platform: Platform) -> list[UUID]:
        return [
            transfer_id
            for transfer_id, transfer in self._storage.items()
            if transfer.user_id == user_id
            and transfer.status is TransferStatus.PAUSED_CLIENT
            and platform
            in (source_platform(transfer.source), destination_platform(transfer.destination))
        ]

    async def client_paused_platforms(self, user_id: UUID) -> set[Platform]:
        platforms: set[Platform] = set()
        for transfer in self._storage.values():
            if transfer.user_id == user_id and transfer.status is TransferStatus.PAUSED_CLIENT:
                platforms.update(
                    p
                    for p in (
                        source_platform(transfer.source),
                        destination_platform(transfer.destination),
                    )
                    if p is not None
                )
        return platforms

    async def pending_positions(self, transfer_id: UUID) -> list[int]:
        stored = self._storage[transfer_id]
        return [i.position for i in stored.items if i.status is TransferItemStatus.PENDING]

    async def find_overdue_paused(self, due_before: datetime) -> list[UUID]:
        return [
            transfer_id
            for transfer_id, transfer in self._storage.items()
            if transfer.status is TransferStatus.PAUSED_QUOTA
            and transfer.resume_at is not None
            and transfer.resume_at < due_before
        ]

    async def find_stale_ids(
        self, statuses: Sequence[TransferStatus], older_than: datetime
    ) -> list[UUID]:
        return [
            transfer_id
            for transfer_id, transfer in self._storage.items()
            if transfer.status in statuses
            and self._updated_at.get(transfer_id, datetime.now(UTC)) < older_than
        ]

    def mark_stale(self, transfer_id: UUID, when: datetime) -> None:
        """Тестовый хелпер: подставить updated_at — имитирует "давно не трогали"."""
        self._updated_at[transfer_id] = when


class FakeUnitOfWork:
    def __init__(self, event_publisher: FakeEventPublisher | None = None) -> None:
        self._tracked: list[AggregateRoot] = []
        self._event_publisher = event_publisher
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    def track(self, aggregate: AggregateRoot) -> None:
        if aggregate not in self._tracked:
            self._tracked.append(aggregate)

    async def commit(self) -> None:
        self.commits += 1
        for aggregate in self._tracked:
            events = aggregate.pull_domain_events()
            if self._event_publisher is not None:
                for event in events:
                    await self._event_publisher.publish(
                        f"transfers:{aggregate.id}", {"type": type(event).__name__}
                    )
        self._tracked.clear()

    async def rollback(self) -> None:
        self.rollbacks += 1
        self._tracked.clear()

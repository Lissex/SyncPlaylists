import dataclasses
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID

from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.value_objects import (
    TransferItemStatus,
    TransferProgress,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.base import AggregateRoot


class FakeEventPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, payload: Mapping[str, Any]) -> None:
        self.published.append((topic, dict(payload)))


class FakeTaskQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue(self, task_name: str, *args: Any, **kwargs: Any) -> None:
        self.enqueued.append((task_name, args))


class FakeTransferRepository:
    def __init__(self) -> None:
        self._storage: dict[UUID, Transfer] = {}
        self._updated_at: dict[UUID, datetime] = {}
        self.save_calls = 0
        self.item_outcome_calls = 0

    async def get(self, transfer_id: UUID) -> Transfer | None:
        return self._storage.get(transfer_id)

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

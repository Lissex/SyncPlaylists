import dataclasses
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.shared_kernel.application.ports import EventPublisher
from syncplaylists.shared_kernel.domain.base import AggregateRoot


class SqlUnitOfWork:
    """После commit() публикует события каждого затронутого (tracked) агрегата.
    Известный долг: нет transactional outbox — если publish упадёт уже после
    успешного session.commit(), событие теряется безвозвратно (at-most-once).
    Для live-прогресса по SSE это осознанно приемлемо — клиент при реконнекте
    получит снэпшот через GetTransferUseCase. См. ARCHITECTURE.md.
    """

    def __init__(self, session: AsyncSession, event_publisher: EventPublisher) -> None:
        self._session = session
        self._event_publisher = event_publisher
        self._tracked: list[AggregateRoot] = []

    async def __aenter__(self) -> "SqlUnitOfWork":
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
        await self._session.commit()
        for aggregate in self._tracked:
            topic = f"{type(aggregate).__name__.lower()}:{aggregate.id}"
            for event in aggregate.pull_domain_events():
                payload = dataclasses.asdict(event)
                payload["type"] = type(event).__name__
                await self._event_publisher.publish(topic, payload)
        self._tracked.clear()

    async def rollback(self) -> None:
        await self._session.rollback()
        self._tracked.clear()

from collections.abc import Mapping
from types import TracebackType
from typing import Any, Protocol

from syncplaylists.shared_kernel.domain.base import AggregateRoot
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.value_objects import Platform


class UnitOfWork(Protocol):
    """__aexit__ — только защитный rollback при исключении; обычный выход из
    `async with` НИЧЕГО не коммитит сам. commit() нужно звать явно — забытый commit
    молча ничего не сохранит, а не молча всё закоммитит."""

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def track(self, aggregate: AggregateRoot) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class EventPublisher(Protocol):
    async def publish(self, topic: str, payload: Mapping[str, Any]) -> None: ...


class TaskQueue(Protocol):
    async def enqueue(self, task_name: str, *args: Any, **kwargs: Any) -> None: ...


class GatewayFactory(Protocol):
    # TODO(этап 4, accounts): заменить на for_account(ConnectedAccount), когда появится
    # модуль accounts. shared_kernel не должен импортировать modules.accounts.domain —
    # сигнатура должна принимать уже собранный вызывающей стороной VO с credentials,
    # а не доменную сущность чужого контекста.
    def for_platform(self, platform: Platform) -> MusicPlatformGateway: ...

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.modules.accounts.domain.errors import AccountAlreadyConnectedError
from syncplaylists.modules.accounts.domain.value_objects import AccountStatus
from syncplaylists.modules.accounts.infrastructure.mappers import account_to_domain, apply_to_orm
from syncplaylists.modules.accounts.infrastructure.orm import ConnectedAccountOrm
from syncplaylists.shared_kernel.domain.value_objects import Platform


async def _save(session: AsyncSession, account: ConnectedAccount) -> None:
    orm = await session.get(ConnectedAccountOrm, account.id)
    if orm is None:
        session.add(apply_to_orm(account, ConnectedAccountOrm()))
    else:
        apply_to_orm(account, orm)


class SqlConnectedAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, account_id: UUID) -> ConnectedAccount | None:
        orm = await self._session.get(ConnectedAccountOrm, account_id)
        return account_to_domain(orm) if orm is not None else None

    async def find_by_external(
        self, user_id: UUID, platform: Platform, external_user_id: str
    ) -> ConnectedAccount | None:
        orm = await self._session.scalar(
            select(ConnectedAccountOrm).where(
                ConnectedAccountOrm.user_id == user_id,
                ConnectedAccountOrm.platform == platform.value,
                ConnectedAccountOrm.external_user_id == external_user_id,
            )
        )
        return account_to_domain(orm) if orm is not None else None

    async def find_active(self, user_id: UUID, platform: Platform) -> ConnectedAccount | None:
        orm = await self._session.scalar(
            select(ConnectedAccountOrm).where(
                ConnectedAccountOrm.user_id == user_id,
                ConnectedAccountOrm.platform == platform.value,
                ConnectedAccountOrm.status == AccountStatus.ACTIVE.value,
            )
        )
        return account_to_domain(orm) if orm is not None else None

    async def list_for_user(self, user_id: UUID) -> list[ConnectedAccount]:
        rows = await self._session.scalars(
            select(ConnectedAccountOrm)
            .where(ConnectedAccountOrm.user_id == user_id)
            .order_by(ConnectedAccountOrm.connected_at)
        )
        return [account_to_domain(orm) for orm in rows]

    async def add(self, account: ConnectedAccount) -> None:
        # SAVEPOINT + flush сразу: гонку двух подключений на одну площадку (частичный
        # UNIQUE «один активный») отдаём доменной ошибкой, а не IntegrityError на commit.
        try:
            async with self._session.begin_nested():
                self._session.add(apply_to_orm(account, ConnectedAccountOrm()))
        except IntegrityError as exc:
            raise AccountAlreadyConnectedError(str(account.platform)) from exc

    async def save(self, account: ConnectedAccount) -> None:
        try:
            async with self._session.begin_nested():
                await _save(self._session, account)
        except IntegrityError as exc:
            raise AccountAlreadyConnectedError(str(account.platform)) from exc


class SqlAccountCredentialsWriter:
    """Своя сессия и транзакция из фабрики — независимо от UoW вызывающего use case."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def apply(self, account_id: UUID, change: Callable[[ConnectedAccount], None]) -> bool:
        async with self._session_factory() as session, session.begin():
            orm = await session.get(ConnectedAccountOrm, account_id, with_for_update=True)
            if orm is None:
                return False
            account = account_to_domain(orm)
            change(account)
            apply_to_orm(account, orm)
        return True

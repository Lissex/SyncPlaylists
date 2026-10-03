from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.identity.domain.entities import User
from syncplaylists.modules.identity.domain.errors import EmailAlreadyRegisteredError
from syncplaylists.modules.identity.domain.value_objects import Email
from syncplaylists.modules.identity.infrastructure.mappers import user_to_domain, user_to_orm
from syncplaylists.modules.identity.infrastructure.orm import UserOrm


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID) -> User | None:
        orm = await self._session.get(UserOrm, user_id)
        return user_to_domain(orm) if orm is not None else None

    async def get_by_email(self, email: Email) -> User | None:
        orm = await self._session.scalar(select(UserOrm).where(UserOrm.email == email.value))
        return user_to_domain(orm) if orm is not None else None

    async def add(self, user: User) -> None:
        # flush сразу и внутри SAVEPOINT: гонку двух регистраций одного email ловим здесь
        # как доменную ошибку, а не как IntegrityError на commit() в use case, и не
        # ломаем внешнюю транзакцию.
        try:
            async with self._session.begin_nested():
                self._session.add(user_to_orm(user))
        except IntegrityError as exc:
            raise EmailAlreadyRegisteredError(user.email.value) from exc

    async def save(self, user: User) -> None:
        await self._session.merge(user_to_orm(user))

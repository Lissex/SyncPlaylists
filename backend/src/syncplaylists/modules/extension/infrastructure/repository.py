from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.extension.domain.entities import ExtensionDevice
from syncplaylists.modules.extension.infrastructure.mappers import (
    device_to_domain,
    device_to_orm,
)
from syncplaylists.modules.extension.infrastructure.orm import ExtensionDeviceOrm


class SqlExtensionDeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, device: ExtensionDevice) -> None:
        self._session.add(device_to_orm(device))
        await self._session.flush()

    async def get(self, device_id: UUID) -> ExtensionDevice | None:
        orm = await self._session.get(ExtensionDeviceOrm, device_id, populate_existing=True)
        return device_to_domain(orm) if orm is not None else None

    async def find_by_token_hash(self, token_hash: str) -> ExtensionDevice | None:
        orm = await self._session.scalar(
            select(ExtensionDeviceOrm)
            .where(ExtensionDeviceOrm.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        return device_to_domain(orm) if orm is not None else None

    async def list_for_user(self, user_id: UUID) -> list[ExtensionDevice]:
        rows = await self._session.scalars(
            select(ExtensionDeviceOrm)
            .where(ExtensionDeviceOrm.user_id == user_id)
            .order_by(ExtensionDeviceOrm.created_at.desc())
        )
        return [device_to_domain(orm) for orm in rows]

    async def save(self, device: ExtensionDevice) -> None:
        await self._session.merge(device_to_orm(device))

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from syncplaylists.modules.catalog.application.ports import PlatformTrackRepository
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import TransferStatus
from syncplaylists.modules.transfers.infrastructure.mappers import (
    transfer_to_domain,
    transfer_to_orm,
)
from syncplaylists.modules.transfers.infrastructure.orm import TransferOrm


class SqlTransferRepository:
    def __init__(self, session: AsyncSession, platform_tracks: PlatformTrackRepository) -> None:
        self._session = session
        self._platform_tracks = platform_tracks

    async def get(self, transfer_id: UUID) -> Transfer | None:
        orm = await self._session.scalar(
            select(TransferOrm)
            .where(TransferOrm.id == transfer_id)
            .options(selectinload(TransferOrm.items))
        )
        return await transfer_to_domain(orm, self._platform_tracks) if orm is not None else None

    async def get_for_update(self, transfer_id: UUID) -> Transfer | None:
        orm = await self._session.scalar(
            select(TransferOrm)
            .where(TransferOrm.id == transfer_id)
            .options(selectinload(TransferOrm.items))
            .with_for_update()
        )
        return await transfer_to_domain(orm, self._platform_tracks) if orm is not None else None

    async def save(self, transfer: Transfer) -> None:
        orm = await transfer_to_orm(transfer, self._platform_tracks)
        await self._session.merge(orm)

    async def find_stale_ids(
        self, statuses: Sequence[TransferStatus], older_than: datetime
    ) -> list[UUID]:
        rows = await self._session.scalars(
            select(TransferOrm.id).where(
                TransferOrm.status.in_([status.value for status in statuses]),
                TransferOrm.updated_at < older_than,
            )
        )
        return list(rows)

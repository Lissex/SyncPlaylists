from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from syncplaylists.modules.catalog.application.ports import PlatformTrackRepository
from syncplaylists.modules.transfers.application.ports import ProgressSample
from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.value_objects import (
    TransferItemStatus,
    TransferProgress,
    TransferStatus,
)
from syncplaylists.modules.transfers.infrastructure.mappers import (
    item_result_columns,
    item_to_domain,
    transfer_header_to_domain,
    transfer_to_domain,
    transfer_to_orm,
)
from syncplaylists.modules.transfers.infrastructure.orm import TransferItemOrm, TransferOrm

# Какой счётчик transfers растёт при переходе item из PENDING в этот статус.
_COUNTER_FOR_STATUS = {
    TransferItemStatus.MATCHED: TransferOrm.matched,
    TransferItemStatus.UNCERTAIN: TransferOrm.uncertain,
    TransferItemStatus.NOT_FOUND: TransferOrm.not_found,
    TransferItemStatus.FAILED: TransferOrm.failed,
}


class SqlTransferRepository:
    def __init__(self, session: AsyncSession, platform_tracks: PlatformTrackRepository) -> None:
        self._session = session
        self._platform_tracks = platform_tracks
        # id «шапок» (get_header) — их нельзя отдавать в полный save(): без items
        # merge с cascade delete-orphan снёс бы все transfer_items.
        self._headers: set[UUID] = set()

    async def get(self, transfer_id: UUID) -> Transfer | None:
        orm = await self._session.scalar(
            select(TransferOrm)
            .where(TransferOrm.id == transfer_id)
            .options(selectinload(TransferOrm.items))
            .execution_options(populate_existing=True)
        )
        return await transfer_to_domain(orm, self._platform_tracks) if orm is not None else None

    async def get_for_update(self, transfer_id: UUID) -> Transfer | None:
        orm = await self._session.scalar(
            select(TransferOrm)
            .where(TransferOrm.id == transfer_id)
            .options(selectinload(TransferOrm.items))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await transfer_to_domain(orm, self._platform_tracks) if orm is not None else None

    async def save(self, transfer: Transfer) -> None:
        if transfer.id in self._headers:
            raise RuntimeError(
                f"Transfer {transfer.id} загружен через get_header() — полный save() запрещён"
            )
        orm = await transfer_to_orm(transfer, self._platform_tracks)
        await self._session.merge(orm)

    async def progress_sample(self, transfer_id: UUID, recent: int) -> ProgressSample | None:
        header = (
            await self._session.execute(
                select(
                    TransferOrm.user_id,
                    TransferOrm.status,
                    TransferOrm.total,
                    TransferOrm.pending,
                    TransferOrm.matched,
                    TransferOrm.uncertain,
                    TransferOrm.not_found,
                    TransferOrm.added,
                    TransferOrm.failed,
                ).where(TransferOrm.id == transfer_id)
            )
        ).one_or_none()
        if header is None:
            return None
        processed = (
            await self._session.scalars(
                select(TransferItemOrm.processed_at)
                .where(
                    TransferItemOrm.transfer_id == transfer_id,
                    TransferItemOrm.processed_at.is_not(None),
                )
                .order_by(TransferItemOrm.processed_at.desc())
                .limit(recent)
            )
        ).all()
        return ProgressSample(
            user_id=header.user_id,
            status=TransferStatus(header.status),
            progress=TransferProgress(
                total=header.total,
                pending=header.pending,
                matched=header.matched,
                uncertain=header.uncertain,
                not_found=header.not_found,
                added=header.added,
                failed=header.failed,
            ),
            recent_processed_at=tuple(at for at in processed if at is not None),
        )

    async def get_header(self, transfer_id: UUID) -> Transfer | None:
        orm = await self._session.scalar(
            select(TransferOrm)
            .where(TransferOrm.id == transfer_id)
            .execution_options(populate_existing=True)
        )
        if orm is None:
            return None
        self._headers.add(transfer_id)
        return transfer_header_to_domain(orm)

    async def get_item(self, transfer_id: UUID, position: int) -> TransferItem | None:
        orm = await self._session.scalar(
            select(TransferItemOrm)
            .where(TransferItemOrm.transfer_id == transfer_id, TransferItemOrm.position == position)
            .execution_options(populate_existing=True)
        )
        return await item_to_domain(orm, self._platform_tracks) if orm is not None else None

    async def save_item_outcome(self, item: TransferItem) -> TransferProgress | None:
        counter = _COUNTER_FOR_STATUS.get(item.status)
        if counter is None:
            raise ValueError(f"save_item_outcome: недопустимый статус item {item.status}")

        # Условный UPDATE — защита от повторной доставки и гонок: из PENDING item
        # переводит ровно одна транзакция, вторая получит 0 строк (после коммита первой).
        updated = await self._session.execute(
            update(TransferItemOrm)
            .where(
                TransferItemOrm.transfer_id == item.transfer_id,
                TransferItemOrm.position == item.position,
                TransferItemOrm.status == TransferItemStatus.PENDING.value,
            )
            .values(**item_result_columns(item), processed_at=func.now())
            .returning(TransferItemOrm.id)
        )
        if updated.first() is None:
            return None

        # Атомарный инкремент на стороне БД, не read-modify-write: параллельные run_match
        # одного переноса сериализуются только на этой короткой операции (лок строки
        # transfers до коммита) и не теряют чужие изменения. Это последняя запись в
        # транзакции run_match перед смещением статуса/commit — порядок локов один и тот
        # же у всех джоб, дедлоков нет.
        row = (
            await self._session.execute(
                update(TransferOrm)
                .where(TransferOrm.id == item.transfer_id)
                .values({TransferOrm.pending: TransferOrm.pending - 1, counter: counter + 1})
                .returning(
                    TransferOrm.total,
                    TransferOrm.pending,
                    TransferOrm.matched,
                    TransferOrm.uncertain,
                    TransferOrm.not_found,
                    TransferOrm.added,
                    TransferOrm.failed,
                )
            )
        ).one()
        return TransferProgress(
            total=row.total,
            pending=row.pending,
            matched=row.matched,
            uncertain=row.uncertain,
            not_found=row.not_found,
            added=row.added,
            failed=row.failed,
        )

    async def transition_status(
        self, transfer_id: UUID, from_status: TransferStatus, to_status: TransferStatus
    ) -> bool:
        result = await self._session.execute(
            update(TransferOrm)
            .where(TransferOrm.id == transfer_id, TransferOrm.status == from_status.value)
            .values(status=to_status.value)
            .returning(TransferOrm.id)
        )
        return result.first() is not None

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

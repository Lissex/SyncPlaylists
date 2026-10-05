from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from syncplaylists.modules.catalog.application.ports import PlatformTrackRepository
from syncplaylists.modules.transfers.application.ports import ClientPause, ProgressSample
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
from syncplaylists.shared_kernel.domain.value_objects import Platform

# Какой счётчик transfers растёт при переходе item из PENDING в этот статус.
# Фазы с запросами к площадке — из них переносы уходят на паузу по квоте.
_QUOTA_PAUSABLE = (TransferStatus.QUEUED, TransferStatus.RUNNING, TransferStatus.WRITING)

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
                    TransferOrm.resume_at,
                    TransferOrm.pause_reason,
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
            resume_at=header.resume_at,
            pause_reason=header.pause_reason,
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

    async def pause_for_quota(self, transfer_id: UUID, resume_at: datetime) -> datetime | None:
        paused = TransferStatus.PAUSED_QUOTA.value
        result = await self._session.execute(
            update(TransferOrm)
            .where(
                TransferOrm.id == transfer_id,
                TransferOrm.status.in_([s.value for s in _QUOTA_PAUSABLE] + [paused]),
            )
            .values(
                paused_from=case(
                    (TransferOrm.status == paused, TransferOrm.paused_from),
                    else_=TransferOrm.status,
                ),
                status=paused,
                resume_at=func.greatest(func.coalesce(TransferOrm.resume_at, resume_at), resume_at),
            )
            .returning(TransferOrm.resume_at)
        )
        row = result.first()
        return row[0] if row is not None else None

    async def resume_from_quota(self, transfer_id: UUID, now: datetime) -> TransferStatus | None:
        result = await self._session.execute(
            update(TransferOrm)
            .where(
                TransferOrm.id == transfer_id,
                TransferOrm.status == TransferStatus.PAUSED_QUOTA.value,
                TransferOrm.resume_at <= now,
            )
            .values(status=TransferOrm.paused_from, paused_from=None, resume_at=None)
            .returning(TransferOrm.status)
        )
        row = result.first()
        return TransferStatus(row[0]) if row is not None else None

    async def pause_for_client(self, transfer_id: UUID, reason: str) -> ClientPause | None:
        paused = TransferStatus.PAUSED_CLIENT.value
        # Прежняя причина нужна вызывающему (событие — только если она изменилась), а
        # UPDATE ... RETURNING отдаёт уже новые значения: читаем её под тем же локом.
        previous = (
            await self._session.execute(
                select(TransferOrm.status, TransferOrm.pause_reason)
                .where(TransferOrm.id == transfer_id)
                .with_for_update()
            )
        ).one_or_none()
        pausable = {s.value for s in _QUOTA_PAUSABLE} | {paused}
        if previous is None or previous.status not in pausable:
            return None
        await self._session.execute(
            update(TransferOrm)
            .where(TransferOrm.id == transfer_id)
            .values(
                paused_from=case(
                    (TransferOrm.status == paused, TransferOrm.paused_from),
                    else_=TransferOrm.status,
                ),
                status=paused,
                pause_reason=reason,
            )
        )
        return ClientPause(previous.pause_reason if previous.status == paused else None)

    async def resume_from_client(self, transfer_id: UUID) -> TransferStatus | None:
        result = await self._session.execute(
            update(TransferOrm)
            .where(
                TransferOrm.id == transfer_id,
                TransferOrm.status == TransferStatus.PAUSED_CLIENT.value,
            )
            .values(status=TransferOrm.paused_from, paused_from=None, pause_reason=None)
            .returning(TransferOrm.status)
        )
        row = result.first()
        return TransferStatus(row[0]) if row is not None else None

    async def find_client_paused(self, user_id: UUID, platform: Platform) -> list[UUID]:
        rows = await self._session.scalars(
            select(TransferOrm.id).where(
                TransferOrm.user_id == user_id,
                TransferOrm.status == TransferStatus.PAUSED_CLIENT.value,
                (TransferOrm.source_platform == platform.value)
                | (TransferOrm.target_platform == platform.value),
            )
        )
        return list(rows)

    async def pending_positions(self, transfer_id: UUID) -> list[int]:
        rows = await self._session.scalars(
            select(TransferItemOrm.position)
            .where(
                TransferItemOrm.transfer_id == transfer_id,
                TransferItemOrm.status == TransferItemStatus.PENDING.value,
            )
            .order_by(TransferItemOrm.position)
        )
        return list(rows)

    async def find_overdue_paused(self, due_before: datetime) -> list[UUID]:
        rows = await self._session.scalars(
            select(TransferOrm.id).where(
                TransferOrm.status == TransferStatus.PAUSED_QUOTA.value,
                TransferOrm.resume_at < due_before,
            )
        )
        return list(rows)

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

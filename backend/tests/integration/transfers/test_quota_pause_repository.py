"""Условные UPDATE паузы по квоте на настоящем Postgres: срок только сдвигается позже,
фаза запоминается, resume срабатывает только после resume_at (этап 4b-3)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.infrastructure.repository import SqlPlatformTrackRepository
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    PlaylistSource,
    TransferStatus,
)
from syncplaylists.modules.transfers.infrastructure.repository import SqlTransferRepository
from syncplaylists.shared_kernel.domain.value_objects import Platform, PlaylistRef


async def _saved_transfer(repo: SqlTransferRepository, user_id: UUID) -> Transfer:
    transfer = Transfer(
        id=uuid4(),
        user_id=user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, f"src-{uuid4().hex[:8]}")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
    )
    transfer.start(datetime.now(UTC))
    await repo.save(transfer)
    return transfer


async def test_pause_extend_and_resume(session: AsyncSession, user_id: UUID) -> None:
    repo = SqlTransferRepository(session, SqlPlatformTrackRepository(session))
    transfer = await _saved_transfer(repo, user_id)
    await session.flush()
    now = datetime.now(UTC)

    first = await repo.pause_for_quota(transfer.id, now + timedelta(minutes=10))
    assert first == now + timedelta(minutes=10)
    # Более ранний срок паузу не сокращает, более поздний — продлевает.
    assert await repo.pause_for_quota(transfer.id, now + timedelta(minutes=5)) == first
    later = await repo.pause_for_quota(transfer.id, now + timedelta(minutes=20))
    assert later == now + timedelta(minutes=20)

    header = await repo.get_header(transfer.id)
    assert header is not None
    assert header.status is TransferStatus.PAUSED_QUOTA
    assert header.paused_from is TransferStatus.RUNNING  # фаза не затёрта продлением

    sample = await repo.progress_sample(transfer.id, recent=1)
    assert sample is not None
    assert sample.resume_at == later

    assert await repo.resume_from_quota(transfer.id, now) is None  # ещё рано
    assert await repo.find_overdue_paused(now + timedelta(minutes=30)) == [transfer.id]
    assert await repo.resume_from_quota(transfer.id, now + timedelta(minutes=21)) is (
        TransferStatus.RUNNING
    )
    resumed = await repo.get_header(transfer.id)
    assert resumed is not None
    assert (resumed.status, resumed.resume_at, resumed.paused_from) == (
        TransferStatus.RUNNING,
        None,
        None,
    )


async def test_finished_transfer_is_not_paused(session: AsyncSession, user_id: UUID) -> None:
    repo = SqlTransferRepository(session, SqlPlatformTrackRepository(session))
    transfer = await _saved_transfer(repo, user_id)
    await session.flush()
    assert await repo.transition_status(transfer.id, TransferStatus.RUNNING, TransferStatus.DONE)

    assert await repo.pause_for_quota(transfer.id, datetime.now(UTC)) is None

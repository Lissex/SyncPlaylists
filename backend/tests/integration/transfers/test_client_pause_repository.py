"""Условные UPDATE ожидания расширения (PAUSED_CLIENT) на настоящем Postgres: фаза
запоминается, причина обновляется, возобновление возвращает фазу; поиск ждущих по
пользователю и площадке (этап 4c)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.infrastructure.repository import SqlPlatformTrackRepository
from syncplaylists.modules.transfers.application.ports import ClientPause
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
        source=PlaylistSource(ref=PlaylistRef(Platform.YANDEX, f"src-{uuid4().hex[:8]}")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SOUNDCLOUD, "123")),
    )
    transfer.start(datetime.now(UTC))
    await repo.save(transfer)
    return transfer


async def test_pause_update_reason_and_resume(session: AsyncSession, user_id: UUID) -> None:
    repo = SqlTransferRepository(session, SqlPlatformTrackRepository(session))
    transfer = await _saved_transfer(repo, user_id)
    await session.flush()

    assert await repo.pause_for_client(transfer.id, "offline") == ClientPause(None)
    assert await repo.pause_for_client(transfer.id, "captcha") == ClientPause("offline")

    header = await repo.get_header(transfer.id)
    assert header is not None
    assert (header.status, header.paused_from, header.pause_reason) == (
        TransferStatus.PAUSED_CLIENT,
        TransferStatus.RUNNING,  # фаза не затёрта сменой причины
        "captcha",
    )
    sample = await repo.progress_sample(transfer.id, recent=1)
    assert sample is not None
    assert sample.pause_reason == "captcha"

    assert await repo.find_client_paused(user_id, Platform.SOUNDCLOUD) == [transfer.id]
    assert await repo.find_client_paused(user_id, Platform.YANDEX) == [transfer.id]  # источник
    assert await repo.find_client_paused(user_id, Platform.VK) == []
    assert await repo.find_client_paused(uuid4(), Platform.SOUNDCLOUD) == []

    assert await repo.resume_from_client(transfer.id) is TransferStatus.RUNNING
    assert await repo.resume_from_client(transfer.id) is None  # повторный сигнал — no-op
    resumed = await repo.get_header(transfer.id)
    assert resumed is not None
    assert (resumed.status, resumed.paused_from, resumed.pause_reason) == (
        TransferStatus.RUNNING,
        None,
        None,
    )


async def test_finished_transfer_is_not_paused(session: AsyncSession, user_id: UUID) -> None:
    repo = SqlTransferRepository(session, SqlPlatformTrackRepository(session))
    transfer = await _saved_transfer(repo, user_id)
    await session.flush()
    assert await repo.transition_status(transfer.id, TransferStatus.RUNNING, TransferStatus.DONE)

    assert await repo.pause_for_client(transfer.id, "offline") is None


async def test_full_save_keeps_pause_fields(session: AsyncSession, user_id: UUID) -> None:
    repo = SqlTransferRepository(session, SqlPlatformTrackRepository(session))
    transfer = await _saved_transfer(repo, user_id)
    transfer.pause_for_client("logged_out", datetime.now(UTC))
    await repo.save(transfer)
    await session.flush()

    loaded = await repo.get(transfer.id)
    assert loaded is not None
    assert (loaded.status, loaded.paused_from, loaded.pause_reason) == (
        TransferStatus.PAUSED_CLIENT,
        TransferStatus.RUNNING,
        "logged_out",
    )

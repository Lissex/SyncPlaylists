from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.domain.entities import PlatformTrack
from syncplaylists.modules.catalog.infrastructure.repository import SqlPlatformTrackRepository
from syncplaylists.modules.transfers.domain.value_objects import TransferItemStatus, TransferStatus
from syncplaylists.modules.transfers.infrastructure.orm import TransferItemOrm, TransferOrm
from syncplaylists.shared_kernel.domain.value_objects import Platform

_SOURCE = {"source_kind": "playlist", "source_platform": "vk", "source_playlist_id": "p"}
_DESTINATION = {
    "destination_kind": "existing",
    "target_platform": "spotify",
    "target_playlist_id": "p",
}


@pytest.mark.parametrize("status", list(TransferStatus))
async def test_every_transfer_status_enum_value_is_accepted(
    session: AsyncSession, status: TransferStatus
) -> None:
    session.add(
        TransferOrm(id=uuid4(), user_id=uuid4(), status=status.value, **_SOURCE, **_DESTINATION)
    )
    await session.flush()


async def test_arbitrary_transfer_status_is_rejected_by_db(session: AsyncSession) -> None:
    session.add(
        TransferOrm(
            id=uuid4(), user_id=uuid4(), status="not_a_real_status", **_SOURCE, **_DESTINATION
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.parametrize("status", list(TransferItemStatus))
async def test_every_transfer_item_status_enum_value_is_accepted(
    session: AsyncSession, status: TransferItemStatus
) -> None:
    transfer_id = uuid4()
    session.add(
        TransferOrm(id=transfer_id, user_id=uuid4(), status="running", **_SOURCE, **_DESTINATION)
    )
    platform_tracks = SqlPlatformTrackRepository(session)
    pt = await platform_tracks.get_or_create(
        PlatformTrack(
            id=uuid4(),
            platform=Platform.VK,
            external_id=f"src-{uuid4().hex[:8]}",
            raw_title="A",
            raw_artist="B",
        )
    )
    await session.flush()

    session.add(
        TransferItemOrm(
            id=uuid4(),
            transfer_id=transfer_id,
            position=0,
            source_pt_id=pt.id,
            status=status.value,
            candidates=[],
        )
    )
    await session.flush()


async def test_arbitrary_transfer_item_status_is_rejected_by_db(session: AsyncSession) -> None:
    transfer_id = uuid4()
    session.add(
        TransferOrm(id=transfer_id, user_id=uuid4(), status="running", **_SOURCE, **_DESTINATION)
    )
    platform_tracks = SqlPlatformTrackRepository(session)
    pt = await platform_tracks.get_or_create(
        PlatformTrack(
            id=uuid4(),
            platform=Platform.VK,
            external_id=f"src-{uuid4().hex[:8]}",
            raw_title="A",
            raw_artist="B",
        )
    )
    await session.flush()

    session.add(
        TransferItemOrm(
            id=uuid4(),
            transfer_id=transfer_id,
            position=0,
            source_pt_id=pt.id,
            status="not_a_real_status",
            candidates=[],
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()

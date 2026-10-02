from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.domain.entities import PlatformTrack
from syncplaylists.modules.catalog.infrastructure.repository import SqlPlatformTrackRepository
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    MatchResult,
    PlaylistSource,
)
from syncplaylists.modules.transfers.infrastructure.repository import SqlTransferRepository
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)


def _transfer() -> Transfer:
    return Transfer(
        id=uuid4(),
        user_id=uuid4(),
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, f"src-{uuid4().hex[:8]}")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, f"dst-{uuid4().hex[:8]}")),
    )


async def test_round_trips_transfer_without_items(session: AsyncSession) -> None:
    platform_tracks = SqlPlatformTrackRepository(session)
    repo = SqlTransferRepository(session, platform_tracks)
    transfer = _transfer()

    await repo.save(transfer)
    await session.flush()

    loaded = await repo.get(transfer.id)
    assert loaded is not None
    assert loaded.id == transfer.id
    assert loaded.user_id == transfer.user_id
    assert loaded.status.value == "queued"
    assert loaded.source == transfer.source
    assert loaded.destination == transfer.destination
    assert loaded.items == []


async def test_round_trips_transfer_with_matched_item(session: AsyncSession) -> None:
    platform_tracks = SqlPlatformTrackRepository(session)
    repo = SqlTransferRepository(session, platform_tracks)
    transfer = _transfer()
    now = datetime.now(UTC)
    transfer.start(now)
    source_ref = ExternalTrackRef(Platform.VK, f"src-track-{uuid4().hex[:8]}")
    transfer.add_item(0, source_ref)
    # transfer_items.source_pt_id — NOT NULL FK, поэтому платформенный трек обязан
    # существовать до сохранения item'а; в проде это делает EnsurePlatformTrackUseCase.
    await platform_tracks.get_or_create(
        PlatformTrack(
            id=uuid4(),
            platform=source_ref.platform,
            external_id=source_ref.external_id,
            raw_title="Starboy",
            raw_artist="The Weeknd",
        )
    )
    target_ref = ExternalTrackRef(Platform.SPOTIFY, f"tgt-track-{uuid4().hex[:8]}")
    transfer.record_match(
        0, MatchResult(target_ref=target_ref, method="fuzzy", score=MatchScore(0.95)), now
    )

    await repo.save(transfer)
    await session.flush()

    loaded = await repo.get(transfer.id)
    assert loaded is not None
    assert loaded.status.value == "running"
    assert len(loaded.items) == 1
    item = loaded.items[0]
    assert item.source_track == source_ref
    assert item.status.value == "matched"
    assert item.match is not None
    assert item.match.target_ref == target_ref
    assert item.match.method == "fuzzy"


async def test_get_for_update_returns_same_data_as_get(session: AsyncSession) -> None:
    platform_tracks = SqlPlatformTrackRepository(session)
    repo = SqlTransferRepository(session, platform_tracks)
    transfer = _transfer()
    await repo.save(transfer)
    await session.flush()

    locked = await repo.get_for_update(transfer.id)
    assert locked is not None
    assert locked.id == transfer.id


async def test_get_returns_none_for_unknown_id(session: AsyncSession) -> None:
    platform_tracks = SqlPlatformTrackRepository(session)
    repo = SqlTransferRepository(session, platform_tracks)

    assert await repo.get(uuid4()) is None

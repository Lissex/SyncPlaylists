"""Этап 5-0 на настоящем Postgres: история (keyset), страница items, методы, пропуск."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.domain.entities import PlatformTrack
from syncplaylists.modules.catalog.infrastructure.repository import SqlPlatformTrackRepository
from syncplaylists.modules.transfers.application.ports import TransferCursor
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    MatchResult,
    PlaylistSource,
    TransferItemStatus,
)
from syncplaylists.modules.transfers.infrastructure.repository import SqlTransferRepository
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    Duration,
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _transfer(user_id: UUID, created_at: datetime, title: str | None = None) -> Transfer:
    return Transfer(
        id=uuid4(),
        user_id=user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, f"src-{uuid4().hex[:8]}")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, f"dst-{uuid4().hex[:8]}")),
        source_title=title,
        created_at=created_at,
    )


async def _seed_track(platform_tracks: SqlPlatformTrackRepository, ref: ExternalTrackRef) -> None:
    await platform_tracks.get_or_create(
        PlatformTrack(
            id=uuid4(),
            platform=ref.platform,
            external_id=ref.external_id,
            raw_title=f"Title {ref.external_id}",
            raw_artist="Artist",
            duration=Duration(200_000),
        )
    )


async def test_history_is_newest_first_and_pages_by_cursor(
    session: AsyncSession, user_id: UUID
) -> None:
    repo = SqlTransferRepository(session, SqlPlatformTrackRepository(session))
    transfers = [_transfer(user_id, _NOW + timedelta(minutes=m), f"#{m}") for m in range(3)]
    for transfer in transfers:
        await repo.save(transfer)
    await session.flush()

    first = await repo.list_summaries(user_id, None, 2)
    assert [s.source_title for s in first] == ["#2", "#1"]
    assert first[0].created_at == _NOW + timedelta(minutes=2)

    rest = await repo.list_summaries(user_id, TransferCursor(first[-1].created_at, first[-1].id), 2)
    assert [s.source_title for s in rest] == ["#0"]
    assert await repo.list_summaries(uuid4(), None, 10) == []


async def test_items_page_filters_and_counts_skipped(session: AsyncSession, user_id: UUID) -> None:
    platform_tracks = SqlPlatformTrackRepository(session)
    repo = SqlTransferRepository(session, platform_tracks)
    transfer = _transfer(user_id, _NOW)
    transfer.start(_NOW)
    refs = [ExternalTrackRef(Platform.VK, f"s-{uuid4().hex[:8]}") for _ in range(4)]
    for position, ref in enumerate(refs):
        await _seed_track(platform_tracks, ref)
        transfer.add_item(position, ref)
    candidate = TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, "cand"), title="T", artist="A"
    )
    transfer.record_match(
        0,
        MatchResult(ExternalTrackRef(Platform.SPOTIFY, "isrc-hit"), "isrc", MatchScore(1.0)),
        _NOW,
    )
    transfer.record_uncertain(1, (candidate,), _NOW)
    transfer.record_uncertain(2, (candidate,), _NOW)
    transfer.record_not_found(3, (), _NOW)
    transfer.enter_review()
    transfer.resolve_item(1, candidate.ref, _NOW)
    transfer.resolve_item(2, None, _NOW)
    await repo.save(transfer)
    await session.flush()

    review = await repo.list_items(
        transfer.id, [TransferItemStatus.SKIPPED, TransferItemStatus.NOT_FOUND], None, 10
    )
    assert [(i.position, i.status) for i in review] == [
        (2, TransferItemStatus.SKIPPED),
        (3, TransferItemStatus.NOT_FOUND),
    ]
    page = await repo.list_items(transfer.id, [], 0, 2)
    assert [i.position for i in page] == [1, 2]

    assert await repo.match_method_counts(transfer.id) == {"isrc": 1, "manual": 1}
    sample = await repo.progress_sample(transfer.id, 0)
    assert sample is not None
    assert (sample.progress.skipped, sample.progress.not_found) == (1, 1)

    found = await platform_tracks.find_by_refs([refs[0], refs[3]])
    assert found[refs[0]].raw_title == f"Title {refs[0].external_id}"
    assert set(found) == {refs[0], refs[3]}

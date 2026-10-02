from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.domain.entities import PlatformTrack
from syncplaylists.modules.catalog.infrastructure.repository import SqlPlatformTrackRepository
from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.modules.matching.infrastructure.repository import SqlTrackMatchRepository
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, MatchScore, Platform


async def test_save_and_find_round_trip(session: AsyncSession) -> None:
    platform_tracks = SqlPlatformTrackRepository(session)
    source_ref = ExternalTrackRef(Platform.VK, f"src-{uuid4().hex[:8]}")
    target_ref = ExternalTrackRef(Platform.SPOTIFY, f"tgt-{uuid4().hex[:8]}")
    await platform_tracks.get_or_create(
        PlatformTrack(
            id=uuid4(),
            platform=source_ref.platform,
            external_id=source_ref.external_id,
            raw_title="Starboy",
            raw_artist="The Weeknd",
        )
    )
    await platform_tracks.get_or_create(
        PlatformTrack(
            id=uuid4(),
            platform=target_ref.platform,
            external_id=target_ref.external_id,
            raw_title="Starboy",
            raw_artist="The Weeknd",
        )
    )
    await session.flush()

    repo = SqlTrackMatchRepository(session, platform_tracks)
    match = TrackMatch(
        id=uuid4(),
        source_ref=source_ref,
        target_platform=target_ref.platform,
        target_ref=target_ref,
        method=MatchMethod.FUZZY,
        score=MatchScore(0.95),
    )
    await repo.save(match)
    await session.flush()

    found = await repo.find(source_ref, target_ref.platform)
    assert found is not None
    assert found.target_ref == target_ref
    assert found.method is MatchMethod.FUZZY
    assert found.confirmations == 0

    await repo.record_confirmation(source_ref, target_ref.platform)
    await session.flush()
    confirmed = await repo.find(source_ref, target_ref.platform)
    assert confirmed is not None
    assert confirmed.confirmations == 1


async def test_find_returns_none_when_source_platform_track_missing(session: AsyncSession) -> None:
    platform_tracks = SqlPlatformTrackRepository(session)
    repo = SqlTrackMatchRepository(session, platform_tracks)

    found = await repo.find(ExternalTrackRef(Platform.VK, "unknown"), Platform.SPOTIFY)

    assert found is None

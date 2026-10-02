from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.domain.entities import CanonicalTrack, PlatformTrack
from syncplaylists.modules.catalog.infrastructure.repository import (
    SqlCanonicalTrackRepository,
    SqlPlatformTrackRepository,
)
from syncplaylists.shared_kernel.domain.value_objects import Duration, ExternalTrackRef, Platform
from tests.integration.helpers import random_isrc


async def test_platform_track_round_trip(session: AsyncSession) -> None:
    repo = SqlPlatformTrackRepository(session)
    ref = ExternalTrackRef(Platform.SPOTIFY, f"tgt-{uuid4().hex[:8]}")
    track = PlatformTrack(
        id=uuid4(),
        platform=ref.platform,
        external_id=ref.external_id,
        raw_title="Starboy",
        raw_artist="The Weeknd",
        duration=Duration(230_000),
    )

    created = await repo.get_or_create(track)
    await session.flush()

    found = await repo.find_by_ref(ref)
    assert found is not None
    assert found.id == created.id
    assert found.raw_title == "Starboy"

    by_id = await repo.find_by_id(created.id)
    assert by_id is not None
    assert by_id.id == created.id


async def test_platform_track_get_or_create_is_idempotent(session: AsyncSession) -> None:
    repo = SqlPlatformTrackRepository(session)
    ref = ExternalTrackRef(Platform.VK, f"src-{uuid4().hex[:8]}")
    track = PlatformTrack(
        id=uuid4(),
        platform=ref.platform,
        external_id=ref.external_id,
        raw_title="A",
        raw_artist="B",
    )

    first = await repo.get_or_create(track)
    await session.flush()
    second = await repo.get_or_create(
        PlatformTrack(
            id=uuid4(),
            platform=ref.platform,
            external_id=ref.external_id,
            raw_title="A",
            raw_artist="B",
        )
    )

    assert first.id == second.id


async def test_canonical_track_round_trip_and_idempotency(session: AsyncSession) -> None:
    repo = SqlCanonicalTrackRepository(session)
    isrc = random_isrc()
    track = CanonicalTrack(id=uuid4(), title_norm="starboy", artist_norm="the weeknd", isrc=isrc)

    first = await repo.get_or_create_by_isrc(track)
    await session.flush()
    second = await repo.get_or_create_by_isrc(
        CanonicalTrack(id=uuid4(), title_norm="starboy", artist_norm="the weeknd", isrc=isrc)
    )

    assert first.id == second.id
    found = await repo.find_by_isrc(isrc)
    assert found is not None
    assert found.id == first.id

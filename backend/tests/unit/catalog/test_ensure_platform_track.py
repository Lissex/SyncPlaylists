from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    Platform,
)
from tests.fakes import FakeCanonicalTrackRepository, FakePlatformTrackRepository


def _use_case() -> EnsurePlatformTrackUseCase:
    return EnsurePlatformTrackUseCase(FakePlatformTrackRepository(), FakeCanonicalTrackRepository())


async def test_is_idempotent_for_same_ref() -> None:
    use_case = _use_case()
    ref = ExternalTrackRef(Platform.SPOTIFY, "tgt-1")

    first = await use_case.execute(ref, "Starboy", "The Weeknd", Duration(230_000))
    second = await use_case.execute(ref, "Starboy", "The Weeknd", Duration(230_000))

    assert first.id == second.id


async def test_links_platform_tracks_with_same_isrc_to_one_canonical() -> None:
    use_case = _use_case()
    isrc = ISRC("USUM71703861")

    spotify_track = await use_case.execute(
        ExternalTrackRef(Platform.SPOTIFY, "tgt-1"), "Starboy", "The Weeknd", isrc=isrc
    )
    vk_track = await use_case.execute(
        ExternalTrackRef(Platform.VK, "src-1"), "Starboy", "The Weeknd", isrc=isrc
    )

    assert spotify_track.canonical_id is not None
    assert spotify_track.canonical_id == vk_track.canonical_id


async def test_without_isrc_has_no_canonical_link() -> None:
    use_case = _use_case()

    track = await use_case.execute(ExternalTrackRef(Platform.VK, "src-1"), "Starboy", "The Weeknd")

    assert track.canonical_id is None

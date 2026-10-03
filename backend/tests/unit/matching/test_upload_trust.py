"""SoundCloud как цель: официальная заливка предпочтительнее перезалива с тем же
названием и длительностью (ARCHITECTURE.md, 11e)."""

from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchRequest, MatchStatus
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.strategies import FuzzySearchStrategy
from syncplaylists.modules.matching.domain.upload_trust import (
    UploadKind,
    UploadTrust,
    UploadVerdict,
)
from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackRestriction
from syncplaylists.shared_kernel.domain.value_objects import (
    Duration,
    ExternalTrackRef,
    MatchScore,
    Platform,
)
from tests.fakes import FakeMusicPlatformGateway


def _sc(
    track_id: str,
    title: str,
    uploader: str,
    *,
    artist: str | None = None,
    rights_holder: bool = False,
    duration_ms: int = 240_000,
    restriction: TrackRestriction | None = None,
) -> TrackCandidate:
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.SOUNDCLOUD, track_id),
        title=title,
        artist=artist or uploader,
        duration=Duration(duration_ms),
        uploader=uploader,
        rights_holder=rights_holder,
        restriction=restriction,
    )


_SOURCE = TrackCandidate(
    ref=ExternalTrackRef(Platform.YANDEX, "1:2"),
    title="Lucid Dreams",
    artist="Juice WRLD",
    duration=Duration(240_000),
)
_ORIGINAL = _sc("100", "Lucid Dreams", "Juice WRLD", artist="Juice WRLD", rights_holder=True)
_REUPLOAD = _sc("200", "Juice WRLD - Lucid Dreams", "trap.tunes.daily")


def _strategy(candidates: list[TrackCandidate]) -> FuzzySearchStrategy:
    normalizer = TrackNormalizer()
    gateway = FakeMusicPlatformGateway(Platform.SOUNDCLOUD, search_results=candidates)
    return FuzzySearchStrategy(gateway, normalizer, MatchScorer(normalizer))


async def _match(candidates: list[TrackCandidate]) -> str | None:
    request = MatchRequest(source=_SOURCE, target_platform=Platform.SOUNDCLOUD)
    attempt = await _strategy(candidates).attempt(request)
    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED
    assert attempt.match is not None
    return attempt.match.target_ref.external_id


async def test_original_beats_reupload_with_same_title_and_duration() -> None:
    # Перезалив первым в выдаче — порядок не должен решать.
    assert await _match([_REUPLOAD, _ORIGINAL]) == "100"
    assert await _match([_ORIGINAL, _REUPLOAD]) == "100"


async def test_uploader_named_like_artist_counts_as_official() -> None:
    by_artist = _sc("300", "Lucid Dreams", "JuiceWRLDofficial")
    assert await _match([_REUPLOAD, by_artist]) == "300"


async def test_lone_reupload_is_still_found() -> None:
    assert await _match([_REUPLOAD]) == "200"


async def test_restriction_of_chosen_candidate_is_kept() -> None:
    snipped = _sc(
        "400",
        "Lucid Dreams",
        "Juice WRLD",
        rights_holder=True,
        restriction=TrackRestriction.PREVIEW_ONLY,
    )
    request = MatchRequest(source=_SOURCE, target_platform=Platform.SOUNDCLOUD)
    attempt = await _strategy([snipped]).attempt(request)
    assert attempt is not None
    assert attempt.match is not None
    assert attempt.match.restriction is TrackRestriction.PREVIEW_ONLY


async def test_bonus_does_not_lift_version_mismatch_to_auto() -> None:
    live_source = TrackCandidate(
        ref=ExternalTrackRef(Platform.YANDEX, "5:6"),
        title="Lucid Dreams (Live)",
        artist="Juice WRLD",
        duration=Duration(240_000),
    )
    request = MatchRequest(source=live_source, target_platform=Platform.SOUNDCLOUD)
    attempt = await _strategy([_ORIGINAL]).attempt(request)
    assert attempt is not None
    assert attempt.status is MatchStatus.UNCERTAIN


def test_classify() -> None:
    trust = UploadTrust()
    plain = TrackCandidate(ref=ExternalTrackRef(Platform.SPOTIFY, "x"), title="t", artist="a")
    assert trust.classify(plain, {"a"}).kind is UploadKind.UNKNOWN
    assert trust.classify(_ORIGINAL, {"juice wrld"}).kind is UploadKind.OFFICIAL
    assert trust.classify(_REUPLOAD, {"juice wrld"}).kind is UploadKind.REUPLOAD
    kino = trust.classify(_sc("1", "Группа крови", "kinoband"), {"кино"})
    assert kino == UploadVerdict(UploadKind.OFFICIAL, uploader_artist="кино")
    label = _sc("2", "Lucid Dreams", "Grade A", rights_holder=True)
    assert trust.classify(label, {"juice wrld"}) == UploadVerdict(UploadKind.OFFICIAL)


def test_adjust_clamps() -> None:
    trust = UploadTrust()
    assert trust.adjust(MatchScore(0.99), UploadKind.OFFICIAL).value == 1.0
    assert trust.adjust(MatchScore(0.01), UploadKind.REUPLOAD).value == 0.0
    assert trust.adjust(MatchScore(0.5), UploadKind.UNKNOWN).value == 0.5

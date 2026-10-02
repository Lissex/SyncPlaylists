from uuid import uuid4

from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchRequest, MatchStatus
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.strategies import (
    CacheStrategy,
    FuzzySearchStrategy,
    IsrcStrategy,
)
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    MatchScore,
    Platform,
)
from tests.unit.matching.fakes import FakeMusicPlatformGateway, FakeTrackMatchRepository

_SOURCE_REF = ExternalTrackRef(Platform.VK, "src-1")


def _request(**overrides: object) -> MatchRequest:
    defaults: dict[str, object] = {
        "ref": _SOURCE_REF,
        "title": "Starboy",
        "artist": "The Weeknd",
        "duration": Duration(230_000),
        "isrc": None,
    }
    defaults.update(overrides)
    source = TrackCandidate(
        ref=defaults["ref"],  # type: ignore[arg-type]
        title=defaults["title"],  # type: ignore[arg-type]
        artist=defaults["artist"],  # type: ignore[arg-type]
        duration=defaults["duration"],  # type: ignore[arg-type]
        isrc=defaults["isrc"],  # type: ignore[arg-type]
    )
    return MatchRequest(source=source, target_platform=Platform.SPOTIFY)


async def test_cache_strategy_passes_through_when_no_entry() -> None:
    strategy = CacheStrategy(FakeTrackMatchRepository())
    assert await strategy.attempt(_request()) is None


async def test_cache_strategy_returns_matched_on_hit() -> None:
    repository = FakeTrackMatchRepository()
    cached = TrackMatch(
        id=uuid4(),
        source_ref=_SOURCE_REF,
        target_platform=Platform.SPOTIFY,
        target_ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
        method=MatchMethod.FUZZY,
        score=MatchScore(0.95),
    )
    await repository.save(cached)

    strategy = CacheStrategy(repository)
    attempt = await strategy.attempt(_request())

    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED
    assert attempt.method is MatchMethod.CACHE
    assert attempt.match is cached


async def test_isrc_strategy_passes_through_when_source_has_no_isrc() -> None:
    strategy = IsrcStrategy(FakeMusicPlatformGateway())
    assert await strategy.attempt(_request(isrc=None)) is None


async def test_isrc_strategy_passes_through_when_search_finds_nothing() -> None:
    gateway = FakeMusicPlatformGateway(isrc_results={})
    strategy = IsrcStrategy(gateway)
    attempt = await strategy.attempt(_request(isrc=ISRC("USRC17607839")))
    assert attempt is None
    assert gateway.search_by_isrc_calls == 1


async def test_isrc_strategy_returns_matched_on_hit() -> None:
    target = TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
        title="Starboy",
        artist="The Weeknd",
        duration=Duration(230_000),
        isrc=ISRC("USRC17607839"),
    )
    gateway = FakeMusicPlatformGateway(isrc_results={"USRC17607839": [target]})
    strategy = IsrcStrategy(gateway)

    attempt = await strategy.attempt(_request(isrc=ISRC("USRC17607839")))

    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED
    assert attempt.method is MatchMethod.ISRC
    assert attempt.match is not None
    assert attempt.match.target_ref == target.ref
    assert attempt.match.score.value == 1.0


async def test_fuzzy_strategy_returns_not_found_when_no_candidates() -> None:
    strategy = FuzzySearchStrategy(
        FakeMusicPlatformGateway(), TrackNormalizer(), MatchScorer(TrackNormalizer())
    )
    attempt = await strategy.attempt(_request())
    assert attempt is not None
    assert attempt.status is MatchStatus.NOT_FOUND


async def test_fuzzy_strategy_returns_matched_for_strong_candidate() -> None:
    candidate = TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
        title="Starboy",
        artist="The Weeknd",
        duration=Duration(230_000),
    )
    gateway = FakeMusicPlatformGateway(search_results=[candidate])
    strategy = FuzzySearchStrategy(gateway, TrackNormalizer(), MatchScorer(TrackNormalizer()))

    attempt = await strategy.attempt(_request())

    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED
    assert attempt.method is MatchMethod.FUZZY
    assert attempt.match is not None
    assert attempt.match.target_ref == candidate.ref


async def test_fuzzy_strategy_returns_uncertain_for_weak_candidate() -> None:
    candidate = TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
        title="Don't You Worry Child",
        artist="Swedish House Mafia",
        duration=Duration(203_000),
    )
    gateway = FakeMusicPlatformGateway(search_results=[candidate])
    strategy = FuzzySearchStrategy(gateway, TrackNormalizer(), MatchScorer(TrackNormalizer()))

    request = _request(
        title="Swedish House Mafia - Don't You Worry Child (Live at Tomorrowland 2023)",
        artist="Swedish House Mafia",
        duration=Duration(372_000),
    )
    attempt = await strategy.attempt(request)

    assert attempt is not None
    assert attempt.status is MatchStatus.UNCERTAIN
    assert attempt.match is None
    assert candidate in attempt.candidates

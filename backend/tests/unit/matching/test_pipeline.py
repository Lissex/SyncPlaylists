from uuid import uuid4

from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchRequest, MatchStatus
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.strategies import build_default_pipeline
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


def _source(**overrides: object) -> TrackCandidate:
    defaults: dict[str, object] = {
        "ref": _SOURCE_REF,
        "title": "Starboy",
        "artist": "The Weeknd",
        "duration": Duration(230_000),
        "isrc": None,
    }
    defaults.update(overrides)
    return TrackCandidate(**defaults)  # type: ignore[arg-type]


async def test_cache_hit_short_circuits_before_gateway_is_called() -> None:
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
    gateway = FakeMusicPlatformGateway()
    pipeline = build_default_pipeline(
        gateway, repository, TrackNormalizer(), MatchScorer(TrackNormalizer())
    )

    attempt = await pipeline.run(MatchRequest(source=_source(), target_platform=Platform.SPOTIFY))

    assert attempt.status is MatchStatus.MATCHED
    assert attempt.method is MatchMethod.CACHE
    assert gateway.search_calls == 0
    assert gateway.search_by_isrc_calls == 0


async def test_isrc_hit_short_circuits_before_fuzzy_search() -> None:
    target = TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
        title="Starboy",
        artist="The Weeknd",
        duration=Duration(230_000),
        isrc=ISRC("USRC17607839"),
    )
    gateway = FakeMusicPlatformGateway(isrc_results={"USRC17607839": [target]})
    pipeline = build_default_pipeline(
        gateway, FakeTrackMatchRepository(), TrackNormalizer(), MatchScorer(TrackNormalizer())
    )

    source = _source(isrc=ISRC("USRC17607839"))
    attempt = await pipeline.run(MatchRequest(source=source, target_platform=Platform.SPOTIFY))

    assert attempt.status is MatchStatus.MATCHED
    assert attempt.method is MatchMethod.ISRC
    assert gateway.search_calls == 0


async def test_strong_fuzzy_match_results_in_matched() -> None:
    candidate = TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
        title="Starboy",
        artist="The Weeknd",
        duration=Duration(230_000),
    )
    gateway = FakeMusicPlatformGateway(search_results=[candidate])
    pipeline = build_default_pipeline(
        gateway, FakeTrackMatchRepository(), TrackNormalizer(), MatchScorer(TrackNormalizer())
    )

    attempt = await pipeline.run(MatchRequest(source=_source(), target_platform=Platform.SPOTIFY))

    assert attempt.status is MatchStatus.MATCHED
    assert attempt.method is MatchMethod.FUZZY


async def test_weak_fuzzy_match_results_in_uncertain_with_candidates() -> None:
    candidate = TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
        title="Don't You Worry Child",
        artist="Swedish House Mafia",
        duration=Duration(203_000),
    )
    gateway = FakeMusicPlatformGateway(search_results=[candidate])
    pipeline = build_default_pipeline(
        gateway, FakeTrackMatchRepository(), TrackNormalizer(), MatchScorer(TrackNormalizer())
    )

    source = _source(
        title="Swedish House Mafia - Don't You Worry Child (Live at Tomorrowland 2023)",
        artist="Swedish House Mafia",
        duration=Duration(372_000),
    )
    attempt = await pipeline.run(MatchRequest(source=source, target_platform=Platform.SPOTIFY))

    assert attempt.status is MatchStatus.UNCERTAIN
    assert attempt.match is None
    assert candidate in attempt.candidates


async def test_no_candidates_anywhere_results_in_not_found() -> None:
    gateway = FakeMusicPlatformGateway()
    pipeline = build_default_pipeline(
        gateway, FakeTrackMatchRepository(), TrackNormalizer(), MatchScorer(TrackNormalizer())
    )

    attempt = await pipeline.run(MatchRequest(source=_source(), target_platform=Platform.SPOTIFY))

    assert attempt.status is MatchStatus.NOT_FOUND

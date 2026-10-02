from uuid import uuid4

from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.matching.application.use_cases import ResolveTrackMatchUseCase
from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchStatus
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.strategies import build_default_pipeline
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    Duration,
    ExternalTrackRef,
    MatchScore,
    Platform,
)
from tests.fakes import (
    FakeCanonicalTrackRepository,
    FakeMusicPlatformGateway,
    FakePlatformTrackRepository,
    FakeTrackMatchRepository,
)

_SOURCE_REF = ExternalTrackRef(Platform.VK, "src-1")


def _source() -> TrackCandidate:
    return TrackCandidate(
        ref=_SOURCE_REF, title="Starboy", artist="The Weeknd", duration=Duration(230_000)
    )


def _ensure_platform_track() -> EnsurePlatformTrackUseCase:
    return EnsurePlatformTrackUseCase(FakePlatformTrackRepository(), FakeCanonicalTrackRepository())


async def test_use_case_persists_new_fuzzy_match() -> None:
    candidate = TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
        title="Starboy",
        artist="The Weeknd",
        duration=Duration(230_000),
    )
    repository = FakeTrackMatchRepository()
    pipeline = build_default_pipeline(
        FakeMusicPlatformGateway(search_results=[candidate]),
        repository,
        TrackNormalizer(),
        MatchScorer(TrackNormalizer()),
    )
    use_case = ResolveTrackMatchUseCase(pipeline, repository, _ensure_platform_track())

    attempt = await use_case.execute(_source(), Platform.SPOTIFY)

    assert attempt.status is MatchStatus.MATCHED
    assert len(repository.save_calls) == 1
    assert repository.save_calls[0].method is MatchMethod.FUZZY


async def test_use_case_does_not_resave_cache_hit() -> None:
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
    repository.save_calls.clear()

    pipeline = build_default_pipeline(
        FakeMusicPlatformGateway(), repository, TrackNormalizer(), MatchScorer(TrackNormalizer())
    )
    use_case = ResolveTrackMatchUseCase(pipeline, repository, _ensure_platform_track())

    attempt = await use_case.execute(_source(), Platform.SPOTIFY)

    assert attempt.status is MatchStatus.MATCHED
    assert attempt.method is MatchMethod.CACHE
    assert repository.save_calls == []

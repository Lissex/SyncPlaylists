from uuid import uuid4

from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchRequest, MatchStatus
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.strategies import (
    CacheStrategy,
    FuzzySearchStrategy,
    IsrcStrategy,
    SamePlatformStrategy,
)
from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    MatchScore,
    Platform,
)
from tests.fakes import FakeMusicPlatformGateway, FakeTrackMatchRepository

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


# --- несовпадение версии: сначала ищем ту же версию, потом оригинал как UNCERTAIN ---


def _target(external_id: str, title: str, duration_ms: int = 260_000) -> TrackCandidate:
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, external_id),
        title=title,
        artist="The Weeknd",
        duration=Duration(duration_ms),
    )


async def test_fuzzy_strategy_searches_same_version_when_first_search_has_only_original() -> None:
    original = _target("orig", "Starboy")
    live = _target("live", "Starboy (Live)")

    def search(query: TrackQuery) -> list[TrackCandidate]:
        return [live] if "live" in query.title else [original]

    gateway = FakeMusicPlatformGateway(search_fn=search)
    strategy = FuzzySearchStrategy(gateway, TrackNormalizer(), MatchScorer(TrackNormalizer()))

    attempt = await strategy.attempt(_request(title="Starboy (Live)", duration=Duration(260_000)))

    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED
    assert attempt.match is not None
    assert attempt.match.target_ref == live.ref
    assert [q.title for q in gateway.search_queries] == ["Starboy (Live)", "starboy live"]
    # Кандидаты нужной версии — первыми (для ручного выбора).
    assert [c.ref for c in attempt.candidates] == [live.ref, original.ref]


async def test_fuzzy_strategy_offers_original_as_uncertain_when_version_missing() -> None:
    original = _target("orig", "Starboy")
    gateway = FakeMusicPlatformGateway(search_results=[original])
    strategy = FuzzySearchStrategy(gateway, TrackNormalizer(), MatchScorer(TrackNormalizer()))

    attempt = await strategy.attempt(_request(title="Starboy (Live)", duration=Duration(260_000)))

    assert attempt is not None
    assert attempt.status is MatchStatus.UNCERTAIN
    assert attempt.match is None
    assert [c.ref for c in attempt.candidates] == [original.ref]
    assert gateway.search_calls == 2  # второй запрос был, но той же версии не нашёл


async def test_fuzzy_strategy_searches_named_remix() -> None:
    original = _target("orig", "Starboy")
    remix = _target("remix", "Starboy (Kygo Remix)")

    def search(query: TrackQuery) -> list[TrackCandidate]:
        return [remix] if "kygo remix" in query.title else [original]

    gateway = FakeMusicPlatformGateway(search_fn=search)
    strategy = FuzzySearchStrategy(gateway, TrackNormalizer(), MatchScorer(TrackNormalizer()))

    attempt = await strategy.attempt(
        _request(title="Starboy (Kygo Remix)", duration=Duration(260_000))
    )

    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED
    assert attempt.match is not None
    assert attempt.match.target_ref == remix.ref


async def test_fuzzy_strategy_no_second_search_for_original_source() -> None:
    gateway = FakeMusicPlatformGateway(search_results=[_target("live", "Starboy (Live)")])
    strategy = FuzzySearchStrategy(gateway, TrackNormalizer(), MatchScorer(TrackNormalizer()))

    await strategy.attempt(_request(title="Starboy", duration=Duration(260_000)))

    assert gateway.search_calls == 1


async def test_fuzzy_strategy_no_second_search_when_same_version_already_found() -> None:
    gateway = FakeMusicPlatformGateway(search_results=[_target("live", "Starboy (Live)")])
    strategy = FuzzySearchStrategy(gateway, TrackNormalizer(), MatchScorer(TrackNormalizer()))

    attempt = await strategy.attempt(_request(title="Starboy (Live)", duration=Duration(260_000)))

    assert gateway.search_calls == 1
    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED


async def test_fuzzy_strategy_version_search_on_empty_first_result() -> None:
    live = _target("live", "Starboy (Live)")

    def search(query: TrackQuery) -> list[TrackCandidate]:
        return [live] if "live" in query.title else []

    gateway = FakeMusicPlatformGateway(search_fn=search)
    strategy = FuzzySearchStrategy(gateway, TrackNormalizer(), MatchScorer(TrackNormalizer()))

    attempt = await strategy.attempt(_request(title="Starboy (Live)", duration=Duration(260_000)))

    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED


# --- одна площадка: без поиска ---------------------------------------------------------


async def test_same_platform_strategy_matches_track_to_itself() -> None:
    request = MatchRequest(source=_request().source, target_platform=Platform.VK)

    attempt = await SamePlatformStrategy().attempt(request)

    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED
    assert attempt.method is MatchMethod.SAME_PLATFORM
    assert attempt.match is not None
    assert attempt.match.target_ref == _SOURCE_REF
    assert attempt.candidates == (request.source,)


async def test_same_platform_strategy_passes_cross_platform_on() -> None:
    assert await SamePlatformStrategy().attempt(_request()) is None  # VK → Spotify

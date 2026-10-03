"""Поисковый запрос и feat-артисты — по реальным промахам e2e SoundCloud → Яндекс
(2026-10-03): в запрос уходил ник заливщика и мусор из названия, а «(feat. X)» выпадал
из артистов и ронял точное совпадение до UNCERTAIN."""

from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchRequest, MatchStatus
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.strategies import FuzzySearchStrategy
from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import (
    Duration,
    ExternalTrackRef,
    Platform,
)
from tests.fakes import FakeMusicPlatformGateway


def _source(title: str, artist: str, duration_ms: int) -> MatchRequest:
    source = TrackCandidate(
        ref=ExternalTrackRef(Platform.SOUNDCLOUD, "1"),
        title=title,
        artist=artist,
        duration=Duration(duration_ms),
    )
    return MatchRequest(source=source, target_platform=Platform.YANDEX)


def _yandex(external_id: str, title: str, artist: str, duration_ms: int) -> TrackCandidate:
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.YANDEX, external_id),
        title=title,
        artist=artist,
        duration=Duration(duration_ms),
    )


def _strategy(gateway: FakeMusicPlatformGateway) -> FuzzySearchStrategy:
    normalizer = TrackNormalizer()
    return FuzzySearchStrategy(gateway, normalizer, MatchScorer(normalizer))


async def _queries(title: str, artist: str) -> list[TrackQuery]:
    gateway = FakeMusicPlatformGateway(Platform.YANDEX)
    await _strategy(gateway).attempt(_source(title, artist, 180_000))
    return gateway.search_queries


async def test_query_takes_artist_from_title_not_uploader() -> None:
    queries = await _queries("AUGUST & FENDIGLOCK – Сам Не Свой", "Finesse Music")
    assert [(q.artist, q.title) for q in queries] == [("august & fendiglock", "сам не свой")]


async def test_query_drops_uploader_junk() -> None:
    queries = await _queries("Gët Busy (prod. Flansie Skimayne)", "YEAT")
    assert [(q.artist, q.title) for q in queries] == [("yeat", "gët busy")]
    queries = await _queries("Mink (Prod Me)", "fakemink")
    assert [(q.artist, q.title) for q in queries] == [("fakemink", "mink")]


async def test_query_keeps_version_and_searches_once() -> None:
    # Версия уже в первом запросе — второй «той же версии» был бы тем же самым.
    queries = await _queries("Starboy (Kygo Remix)", "The Weeknd")
    assert [(q.artist, q.title) for q in queries] == [("the weeknd", "starboy kygo remix")]


def test_featured_artists_join_artist() -> None:
    normalizer = TrackNormalizer()
    assert normalizer.normalize("Меня не будет (feat. SALUKI)", "ANIKV").artist == "anikv, saluki"
    assert normalizer.normalize("Мания (feat. FENDIGLOCK)", "AUGUST").artist == "august, fendiglock"
    # Уже есть в артистах — не дублируем.
    result = normalizer.normalize("Кащенко (feat. PowerpuffLuv)", "Boulevard Depo, PowerpuffLuv")
    assert result.artist == "boulevard depo, powerpuffluv"
    assert result.title == "кащенко"


async def test_exact_track_with_feat_is_auto() -> None:
    target = _yandex("63589631:10130477", "Меня не будет", "ANIKV, SALUKI", 253_000)
    gateway = FakeMusicPlatformGateway(Platform.YANDEX, search_results=[target])

    attempt = await _strategy(gateway).attempt(
        _source("Меня не будет (feat. SALUKI)", "ANIKV", 253_000)
    )

    assert attempt is not None
    assert attempt.status is MatchStatus.MATCHED

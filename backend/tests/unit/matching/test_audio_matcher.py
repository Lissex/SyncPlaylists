"""Поиск на цели по распознанному (этап 6, правила 11j): случаи спайка 6-0 — ремикс,
который Shazam узнал как оригинал, и slowed + reverb, принятый за посторонний трек."""

from syncplaylists.modules.matching.domain.audio import AudioMatcher, AudioVerdict
from syncplaylists.modules.matching.domain.entities import MatchMethod
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchStatus
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.shared_kernel.domain.search import RecognizedTrack, TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    MatchScore,
    Platform,
)
from tests.fakes import FakeMusicPlatformGateway

_ISRC = ISRC("USUG11800685")


def _source(title: str, artist: str = "Juice WRLD", ms: int = 239_836) -> TrackCandidate:
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.SOUNDCLOUD, "src"),
        title=title,
        artist=artist,
        duration=Duration(ms),
    )


def _target(
    title: str,
    artist: str = "Juice WRLD",
    ms: int = 239_836,
    isrc: ISRC | None = None,
    id_: str = "t1",
) -> TrackCandidate:
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.YANDEX, id_),
        title=title,
        artist=artist,
        duration=Duration(ms),
        isrc=isrc,
    )


def _heard(
    title: str = "Lucid Dreams",
    artist: str = "Juice WRLD",
    *,
    exact: bool = True,
    isrc: ISRC | None = _ISRC,
) -> RecognizedTrack:
    return RecognizedTrack(title=title, artist=artist, provider="shazam", isrc=isrc, exact=exact)


def _matcher(gateway: FakeMusicPlatformGateway) -> AudioMatcher:
    normalizer = TrackNormalizer()
    return AudioMatcher(gateway, normalizer, MatchScorer(normalizer))


async def test_isrc_hit_is_found_by_audio() -> None:
    # Источник — перезалив с мусорным названием, Shazam узнал оригинал с ISRC.
    original = _target("Lucid Dreams", isrc=_ISRC)
    gateway = FakeMusicPlatformGateway(Platform.YANDEX, isrc_results={_ISRC.value: [original]})

    outcome = await _matcher(gateway).match(
        _source("lucid dreamz [FREE DL] prod. nick mira", artist="beatstore99"),
        _heard(),
        Platform.YANDEX,
    )

    assert outcome.verdict is AudioVerdict.EXACT
    assert outcome.attempt.status is MatchStatus.MATCHED
    assert outcome.attempt.match is not None
    assert outcome.attempt.match.method is MatchMethod.AUDIO
    assert outcome.attempt.match.target_ref == original.ref
    assert gateway.search_calls == 0  # ISRC хватило


async def test_remix_source_does_not_take_original_by_isrc() -> None:
    # Shazam узнал в ремиксе оригинал (11j): ISRC оригинала не делает его ремиксом.
    original = _target("Lucid Dreams", isrc=_ISRC)
    gateway = FakeMusicPlatformGateway(Platform.YANDEX, isrc_results={_ISRC.value: [original]})

    outcome = await _matcher(gateway).match(
        _source("Lucid Dreams (Remix) ft. Lil Uzi Vert", ms=240_483), _heard(), Platform.YANDEX
    )

    assert outcome.attempt.status is MatchStatus.UNCERTAIN
    assert outcome.attempt.candidates == (original,)
    assert outcome.verdict is AudioVerdict.NOT_ON_TARGET


async def test_remix_found_by_search_with_source_version() -> None:
    remix = _target("Lucid Dreams (Remix) [feat. Lil Uzi Vert]", ms=240_483)
    gateway = FakeMusicPlatformGateway(Platform.YANDEX, search_results=[remix])

    outcome = await _matcher(gateway).match(
        _source("Lucid Dreams (Remix) ft. Lil Uzi Vert", ms=240_483),
        _heard(isrc=None),
        Platform.YANDEX,
    )

    assert outcome.verdict is AudioVerdict.EXACT
    assert outcome.attempt.status is MatchStatus.MATCHED
    # Запрос — распознанное название с версией источника.
    assert gateway.search_queries[0].title == "lucid dreams remix"


async def test_skewed_recognition_is_only_a_hint() -> None:
    # Slowed + reverb → Shazam: «Slater — Forget Me» со сдвигом −4,4 % (11j).
    forget_me = _target("Forget Me", artist="Slater")
    gateway = FakeMusicPlatformGateway(Platform.YANDEX, search_results=[forget_me])

    outcome = await _matcher(gateway).match(
        _source("Juice WRLD - Lucid Dreams (Slowed + Reverb)", artist="trinxchris"),
        _heard("Forget Me", "Slater", exact=False, isrc=None),
        Platform.YANDEX,
    )

    assert outcome.attempt.status is MatchStatus.UNCERTAIN
    assert outcome.attempt.match is None
    assert outcome.verdict is AudioVerdict.ALTERED


async def test_exact_but_contradicting_title_is_a_hint() -> None:
    other = _target("Forget Me", artist="Slater")
    gateway = FakeMusicPlatformGateway(Platform.YANDEX, search_results=[other])

    outcome = await _matcher(gateway).match(
        _source("Lucid Dreams", artist="Juice WRLD"),
        _heard("Forget Me", "Slater", isrc=None),
        Platform.YANDEX,
    )

    assert outcome.attempt.status is MatchStatus.UNCERTAIN
    assert outcome.verdict is AudioVerdict.TEXT_MISMATCH


async def test_unintelligible_source_title_trusts_audio() -> None:
    original = _target("Lucid Dreams", isrc=_ISRC)
    gateway = FakeMusicPlatformGateway(Platform.YANDEX, isrc_results={_ISRC.value: [original]})

    outcome = await _matcher(gateway).match(
        _source("track 07", artist="user123456"), _heard(), Platform.YANDEX
    )

    assert outcome.verdict is AudioVerdict.EXACT


async def test_duration_far_off_is_not_auto_even_with_isrc() -> None:
    extended = _target("Lucid Dreams", isrc=_ISRC, ms=360_000)
    gateway = FakeMusicPlatformGateway(Platform.YANDEX, isrc_results={_ISRC.value: [extended]})

    outcome = await _matcher(gateway).match(_source("Lucid Dreams"), _heard(), Platform.YANDEX)

    assert outcome.attempt.status is MatchStatus.UNCERTAIN


async def test_isrc_empty_on_target_falls_back_to_search() -> None:
    found = _target("Lucid Dreams")
    gateway = FakeMusicPlatformGateway(Platform.YANDEX, search_results=[found])

    outcome = await _matcher(gateway).match(_source("Lucid Dreams"), _heard(), Platform.YANDEX)

    assert gateway.search_by_isrc_calls == 1
    assert gateway.search_calls == 1
    assert outcome.attempt.status is MatchStatus.MATCHED


async def test_nothing_on_target() -> None:
    gateway = FakeMusicPlatformGateway(Platform.YANDEX)

    outcome = await _matcher(gateway).match(_source("Lucid Dreams"), _heard(), Platform.YANDEX)

    assert outcome.attempt.status is MatchStatus.NOT_FOUND
    assert outcome.verdict is AudioVerdict.NOT_ON_TARGET


async def test_cap_below_auto_keeps_low_scores() -> None:
    scorer = MatchScorer(TrackNormalizer())

    assert scorer.cap_below_auto(MatchScore(1.0)).tier.value == "uncertain"
    assert scorer.cap_below_auto(MatchScore(0.3)) == MatchScore(0.3)

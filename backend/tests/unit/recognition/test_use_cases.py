from datetime import UTC, datetime, timedelta
from uuid import uuid4

from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.recognition.application.ports import AudioRecognizer
from syncplaylists.modules.recognition.application.use_cases import (
    GetRecognitionQuery,
    RecognitionDeferred,
    RecognitionDone,
    RecognitionLimits,
    RecognitionUnavailable,
    RecognizeSourceTrackUseCase,
)
from syncplaylists.modules.recognition.domain.entities import Recognition, RecognitionOutcome
from syncplaylists.modules.recognition.domain.errors import (
    AudioTemporarilyUnavailableError,
    AudioUnavailableError,
    NoAudioReason,
    RecognizerBusyError,
    RecognizerUnavailableError,
)
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow
from syncplaylists.shared_kernel.domain.search import RecognizedTrack, TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import Duration, ExternalTrackRef, Platform
from tests.fakes import FakeCanonicalTrackRepository, FakePlatformTrackRepository
from tests.fakes.recognition import (
    FakeAudioSource,
    FakeAudioSourceRegistry,
    FakeRecognitionLock,
    FakeRecognitionRepository,
    FakeRecognizer,
    FakeTrimmer,
)

_NOW = datetime(2026, 10, 12, tzinfo=UTC)
_SOURCE = TrackCandidate(
    ref=ExternalTrackRef(Platform.SOUNDCLOUD, "1057094605"),
    title="Lucid Dreams (Remix) ft. Lil Uzi Vert",
    artist="Juice WRLD",
    duration=Duration(240_483),
)
_TRACK = RecognizedTrack(title="Lucid Dreams", artist="Juice WRLD", provider="shazam", exact=True)
_LIMITS = RecognitionLimits(
    fragment_ms=18_000, no_match_ttl=timedelta(days=30), no_audio_ttl=timedelta(days=7)
)


class Harness:
    def __init__(
        self,
        *recognizers: AudioRecognizer,
        source: FakeAudioSource | None = None,
        trimmer: FakeTrimmer | None = None,
        lock: FakeRecognitionLock | None = None,
    ) -> None:
        self.source = source or FakeAudioSource()
        self.trimmer = trimmer or FakeTrimmer()
        self.repository = FakeRecognitionRepository()
        self.lock = lock or FakeRecognitionLock()
        self.platform_tracks = FakePlatformTrackRepository()
        self.use_case = RecognizeSourceTrackUseCase(
            FakeAudioSourceRegistry(self.source),
            self.trimmer,
            list(recognizers),
            self.repository,
            self.lock,
            EnsurePlatformTrackUseCase(self.platform_tracks, FakeCanonicalTrackRepository()),
            _LIMITS,
            clock=lambda: _NOW,
        )


async def test_recognized_by_first_recognizer() -> None:
    shazam = FakeRecognizer("shazam", _TRACK)
    acr = FakeRecognizer("acrcloud")
    harness = Harness(shazam, acr)

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionDone)
    assert not report.cached
    assert report.recognition.outcome is RecognitionOutcome.RECOGNIZED
    assert report.recognition.track == _TRACK
    assert report.recognition.window == FragmentWindow(111_241, 18_000)
    assert acr.calls == []  # fallback не нужен
    assert harness.source.calls == [(_SOURCE.ref, 18_000, 240_483)]
    assert harness.trimmer.calls[0].data == b"audio"
    assert shazam.calls == [b"wav:audio"]
    assert harness.repository.saved == [report.recognition]
    # Кэш ссылается на platform_tracks — трек источника заведён до записи.
    assert await harness.platform_tracks.find_by_ref(_SOURCE.ref) is not None


async def test_fallback_when_first_does_not_know() -> None:
    acr_track = RecognizedTrack(title="Lucid Dreams", artist="Juice WRLD", provider="acrcloud")
    harness = Harness(FakeRecognizer("shazam", None), FakeRecognizer("acrcloud", acr_track))

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionDone)
    assert report.recognition.track == acr_track


async def test_no_match_is_cached_with_ttl() -> None:
    harness = Harness(FakeRecognizer("shazam", None), FakeRecognizer("acrcloud", None))

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionDone)
    assert report.recognition.outcome is RecognitionOutcome.NO_MATCH
    assert report.recognition.expires_at == _NOW + timedelta(days=30)


async def test_cache_hit_skips_audio_and_recognizers() -> None:
    shazam = FakeRecognizer("shazam", _TRACK)
    harness = Harness(shazam)
    cached = Recognition.recognized(uuid4(), _SOURCE.ref, _TRACK, FragmentWindow(0, 1), _NOW)
    await harness.repository.save(cached)

    report = await harness.use_case.execute(_SOURCE, None)

    assert report == RecognitionDone(cached, cached=True)
    assert harness.source.calls == []
    assert shazam.calls == []


async def test_expired_negative_is_recognized_again() -> None:
    harness = Harness(FakeRecognizer("shazam", _TRACK))
    stale = Recognition.no_match(
        uuid4(), _SOURCE.ref, FragmentWindow(0, 1), _NOW - timedelta(days=31), timedelta(days=30)
    )
    await harness.repository.save(stale)

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionDone)
    assert report.recognition.outcome is RecognitionOutcome.RECOGNIZED


async def test_encrypted_audio_cached_as_no_audio() -> None:
    shazam = FakeRecognizer("shazam", _TRACK)
    harness = Harness(
        shazam, source=FakeAudioSource(error=AudioUnavailableError(NoAudioReason.ENCRYPTED))
    )

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionDone)
    assert report.recognition.outcome is RecognitionOutcome.NO_AUDIO
    assert report.recognition.no_audio_reason is NoAudioReason.ENCRYPTED
    assert report.recognition.expires_at == _NOW + timedelta(days=7)
    assert shazam.calls == []


async def test_undecodable_audio_cached_as_no_audio() -> None:
    harness = Harness(
        FakeRecognizer("shazam", _TRACK),
        trimmer=FakeTrimmer(error=AudioUnavailableError(NoAudioReason.NO_STREAM, "ffmpeg")),
    )

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionDone)
    assert report.recognition.outcome is RecognitionOutcome.NO_AUDIO


async def test_temporary_audio_failure_not_cached() -> None:
    harness = Harness(
        FakeRecognizer("shazam", _TRACK),
        source=FakeAudioSource(error=AudioTemporarilyUnavailableError("таймаут")),
    )

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionUnavailable)
    assert harness.repository.saved == []


async def test_no_audio_source_for_platform() -> None:
    harness = Harness(FakeRecognizer("shazam", _TRACK))
    vk_track = TrackCandidate(ref=ExternalTrackRef(Platform.VK, "1_2"), title="x", artist="y")

    report = await harness.use_case.execute(vk_track, None)

    assert isinstance(report, RecognitionUnavailable)
    assert harness.source.calls == []


async def test_busy_recognizer_defers_instead_of_no_match() -> None:
    # Shazam занят, ACR «не знает» — это ещё не NO_MATCH: Shazam мог бы узнать.
    harness = Harness(
        FakeRecognizer("shazam", RecognizerBusyError("shazam", 120)),
        FakeRecognizer("acrcloud", None),
    )

    report = await harness.use_case.execute(_SOURCE, None)

    assert report == RecognitionDeferred(120)
    assert harness.repository.saved == []


async def test_busy_first_then_second_recognizes() -> None:
    acr_track = RecognizedTrack(title="Lucid Dreams", artist="Juice WRLD", provider="acrcloud")
    harness = Harness(
        FakeRecognizer("shazam", RecognizerBusyError("shazam", 600)),
        FakeRecognizer("acrcloud", acr_track),
    )

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionDone)
    assert report.recognition.track == acr_track


async def test_failed_recognizer_is_unavailable_not_no_match() -> None:
    harness = Harness(FakeRecognizer("shazam", RecognizerUnavailableError("503")))

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionUnavailable)
    assert harness.repository.saved == []


async def test_no_recognizers_configured() -> None:
    harness = Harness()

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionUnavailable)
    assert harness.repository.saved == []


async def test_locked_by_another_worker_defers() -> None:
    shazam = FakeRecognizer("shazam", _TRACK)
    harness = Harness(shazam, lock=FakeRecognitionLock(busy=True))

    report = await harness.use_case.execute(_SOURCE, None)

    assert isinstance(report, RecognitionDeferred)
    assert harness.source.calls == []
    assert shazam.calls == []


async def test_get_recognition_ignores_expired() -> None:
    repository = FakeRecognitionRepository()
    stale = Recognition.no_audio(
        uuid4(), _SOURCE.ref, NoAudioReason.NO_STREAM, _NOW - timedelta(days=8), timedelta(days=7)
    )
    await repository.save(stale)

    assert await GetRecognitionQuery(repository, lambda: _NOW).execute(_SOURCE.ref) is None
    assert (
        await GetRecognitionQuery(repository, lambda: _NOW - timedelta(days=2)).execute(_SOURCE.ref)
        == stale
    )

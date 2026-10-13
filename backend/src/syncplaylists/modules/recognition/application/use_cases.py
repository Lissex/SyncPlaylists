import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.recognition.application.ports import (
    AudioRecognizer,
    AudioSourceRegistry,
    AudioTrimmer,
    RecognitionLock,
    RecognitionRepository,
)
from syncplaylists.modules.recognition.domain.entities import Recognition
from syncplaylists.modules.recognition.domain.errors import (
    AudioTemporarilyUnavailableError,
    AudioUnavailableError,
    RecognizerBusyError,
    RecognizerUnavailableError,
)
from syncplaylists.modules.recognition.domain.fragments import SourceAudio
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.search import RecognizedTrack, TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef

logger = logging.getLogger(__name__)

# Трек уже распознаёт другой воркер — заглянуть позже: к тому времени будет в кэше.
_LOCKED_RETRY_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class RecognitionDone:
    recognition: Recognition
    cached: bool

    @property
    def heard(self) -> RecognizedTrack | None:
        return self.recognition.track

    @property
    def outcome(self) -> str:
        """recognized | no_match | no_audio — для других контекстов без recognition.domain."""
        return self.recognition.outcome.value


@dataclass(frozen=True, slots=True)
class RecognitionDeferred:
    """Распознаватели просят подождать (темп, 429) — повторить задачу позже."""

    retry_after_seconds: float


@dataclass(frozen=True, slots=True)
class RecognitionUnavailable:
    """Сейчас не получилось и в кэш не пишется: нет доступа к звуку, сеть, сбой
    распознавателя. Трек остаётся с текстовым результатом."""

    detail: str


RecognitionReport = RecognitionDone | RecognitionDeferred | RecognitionUnavailable


@dataclass(frozen=True, slots=True)
class RecognitionLimits:
    fragment_ms: int
    no_match_ttl: timedelta
    no_audio_ttl: timedelta


class RecognizeSourceTrackUseCase:
    """Распознать трек источника: кэш → окно звука → wav → распознаватели по очереди.
    Аудио живёт только в памяти этого вызова (и во временных файлах AudioTrimmer внутри
    него) — на диске не остаётся (ARCHITECTURE.md, 11)."""

    def __init__(
        self,
        sources: AudioSourceRegistry,
        trimmer: AudioTrimmer,
        recognizers: Sequence[AudioRecognizer],
        repository: RecognitionRepository,
        lock: RecognitionLock,
        ensure_platform_track: EnsurePlatformTrackUseCase,
        limits: RecognitionLimits,
        clock: Callable[[], datetime],
    ) -> None:
        self._sources = sources
        self._trimmer = trimmer
        self._recognizers = recognizers
        self._repository = repository
        self._lock = lock
        self._ensure_platform_track = ensure_platform_track
        self._limits = limits
        self._clock = clock

    async def execute(
        self, source: TrackCandidate, access: AccountAccess | None
    ) -> RecognitionReport:
        cached = await self._fresh(source.ref)
        if cached is not None:
            return RecognitionDone(cached, cached=True)
        audio_source = self._sources.for_track(source.ref, access)
        if audio_source is None:
            return RecognitionUnavailable("нет доступа к звуку площадки")
        async with self._lock.hold(source.ref) as acquired:
            if not acquired:
                return RecognitionDeferred(_LOCKED_RETRY_SECONDS)
            # Пока ждали лок, трек мог распознать другой воркер.
            cached = await self._fresh(source.ref)
            if cached is not None:
                return RecognitionDone(cached, cached=True)
            await self._ensure_platform_track.execute(
                source.ref, source.title, source.artist, source.duration, source.isrc
            )
            duration_ms = source.duration.milliseconds if source.duration else None
            try:
                audio = await audio_source.fetch(source.ref, self._limits.fragment_ms, duration_ms)
                wav = await self._trimmer.to_wav(audio)
            except AudioUnavailableError as exc:
                recognition = Recognition.no_audio(
                    uuid4(), source.ref, exc.reason, self._clock(), self._limits.no_audio_ttl
                )
                await self._repository.save(recognition)
                return RecognitionDone(recognition, cached=False)
            except AudioTemporarilyUnavailableError as exc:
                return RecognitionUnavailable(f"звук: {exc}")
            return await self._recognize(source.ref, audio, wav)

    async def _recognize(
        self, ref: ExternalTrackRef, audio: SourceAudio, wav: bytes
    ) -> RecognitionReport:
        busy: list[float] = []
        failed: list[str] = []
        for recognizer in self._recognizers:
            try:
                track = await recognizer.recognize(wav)
            except RecognizerBusyError as exc:
                busy.append(exc.retry_after_seconds)
                continue
            except RecognizerUnavailableError as exc:
                logger.warning("Распознаватель %s не ответил: %s", recognizer.provider, exc)
                failed.append(recognizer.provider)
                continue
            if track is not None:
                return await self._save_recognized(ref, audio, track)
        if busy:
            # «Не знаю» от одних при занятых других — ещё не NO_MATCH: занятый мог узнать.
            return RecognitionDeferred(min(busy))
        if failed:
            return RecognitionUnavailable("распознаватель не ответил: " + ", ".join(failed))
        if not self._recognizers:
            return RecognitionUnavailable("распознаватели не настроены")
        recognition = Recognition.no_match(
            uuid4(), ref, audio.window, self._clock(), self._limits.no_match_ttl
        )
        await self._repository.save(recognition)
        return RecognitionDone(recognition, cached=False)

    async def _save_recognized(
        self, ref: ExternalTrackRef, audio: SourceAudio, track: RecognizedTrack
    ) -> RecognitionDone:
        recognition = Recognition.recognized(uuid4(), ref, track, audio.window, self._clock())
        await self._repository.save(recognition)
        return RecognitionDone(recognition, cached=False)

    async def _fresh(self, ref: ExternalTrackRef) -> Recognition | None:
        recognition = await self._repository.find(ref)
        if recognition is not None and recognition.is_fresh(self._clock()):
            return recognition
        return None


class GetRecognitionQuery:
    """Свежее распознавание трека источника (для поиска на цели по распознанному)."""

    def __init__(self, repository: RecognitionRepository, clock: Callable[[], datetime]) -> None:
        self._repository = repository
        self._clock = clock

    async def execute(self, ref: ExternalTrackRef) -> Recognition | None:
        recognition = await self._repository.find(ref)
        if recognition is not None and recognition.is_fresh(self._clock()):
            return recognition
        return None

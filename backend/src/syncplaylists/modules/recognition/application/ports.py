from contextlib import AbstractAsyncContextManager
from typing import Protocol

from syncplaylists.modules.recognition.domain.entities import Recognition
from syncplaylists.modules.recognition.domain.fragments import SourceAudio
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.search import RecognizedTrack
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform


class AudioSource(Protocol):
    """Звук трека площадки. Качает только окно (Range, сегменты окна), не весь файл.
    Бросает AudioUnavailableError (звука нет и не будет: шифрование, нет потока) или
    AudioTemporarilyUnavailableError (сеть, лимит, нет доступа сейчас)."""

    platform: Platform

    async def fetch(
        self, ref: ExternalTrackRef, length_ms: int, duration_ms: int | None
    ) -> SourceAudio: ...


class AudioSourceRegistry(Protocol):
    def supports(self, platform: Platform) -> bool: ...

    # None — для этого трека звук сейчас взять неоткуда (нужен аккаунт, а его нет).
    def for_track(
        self, ref: ExternalTrackRef, access: AccountAccess | None
    ) -> AudioSource | None: ...


class AudioTrimmer(Protocol):
    """Точная вырезка окна → wav 16 кГц mono. Временные файлы — только внутри вызова,
    удаляются и при ошибке. Бросает AudioUnavailableError, если байты не декодируются."""

    async def to_wav(self, audio: SourceAudio) -> bytes: ...


class AudioRecognizer(Protocol):
    """None — распознаватель ответил «не знаю». Бросает RecognizerBusyError (подождать)
    и RecognizerUnavailableError (не ответил)."""

    provider: str

    async def recognize(self, wav: bytes) -> RecognizedTrack | None: ...


class RecognizerThrottle(Protocol):
    """Общий на сервер темп обращений к распознавателю (все воркеры и процессы)."""

    # None — можно идти (ожидание, если было, уже выдержано); число — ждать дольше
    # допустимого, через сколько секунд повторить.
    async def acquire(self, provider: str) -> float | None: ...

    # 429 распознавателя: никто не обращается к нему ближайшие seconds.
    async def penalize(self, provider: str, seconds: float) -> None: ...


class RecognitionRepository(Protocol):
    async def find(self, source_ref: ExternalTrackRef) -> Recognition | None: ...

    # Одна запись на трек источника: новая заменяет прежнюю (устаревший отрицательный
    # исход сменяется свежим).
    async def save(self, recognition: Recognition) -> None: ...


class RecognitionLock(Protocol):
    """Один трек источника распознаёт один воркер: тот же трек из двух переносов не идёт
    в Shazam дважды. Контекст отдаёт False, если трек уже распознаёт кто-то другой."""

    def hold(self, source_ref: ExternalTrackRef) -> AbstractAsyncContextManager[bool]: ...

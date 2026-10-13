from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from syncplaylists.shared_kernel.domain.base import ValueObject
from syncplaylists.shared_kernel.domain.search import RecognizedTrack
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)


class TransferStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_CAPTCHA = "paused_captcha"
    # Площадка исчерпала квоту (429 с долгим Retry-After): перенос ждёт целиком до
    # resume_at, ни один трек из-за этого не уходит в FAILED (этап 4b-3).
    PAUSED_QUOTA = "paused_quota"
    # Ждём браузер пользователя: операцию на площадке выполняет расширение, а оно сейчас
    # не может (закрыт браузер, нет входа, капча). Без срока — продолжит событие
    # «расширение готово» (этап 4c). Ни один трек из-за этого не уходит в FAILED.
    PAUSED_CLIENT = "paused_client"
    REVIEW = "review"
    WRITING = "writing"
    DONE = "done"
    FAILED = "failed"


class TransferItemStatus(StrEnum):
    PENDING = "pending"
    MATCHED = "matched"
    UNCERTAIN = "uncertain"
    NOT_FOUND = "not_found"
    ADDED = "added"
    FAILED = "failed"
    # Пользователь на ревью отказался от трека («Пропустить») или начал запись, не решив
    # его, — не ошибка: в отчёте отдельной строкой от FAILED (сбой сопоставления/записи).
    SKIPPED = "skipped"


class FileFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"
    M3U8 = "m3u8"
    XSPF = "xspf"
    TXT = "txt"


@dataclass(frozen=True, slots=True)
class PlaylistSource(ValueObject):
    ref: PlaylistRef


@dataclass(frozen=True, slots=True)
class LibrarySource(ValueObject):
    platform: Platform
    account_id: UUID


@dataclass(frozen=True, slots=True)
class FileSource(ValueObject):
    file_id: UUID
    format: FileFormat


TrackSource = PlaylistSource | LibrarySource | FileSource


@dataclass(frozen=True, slots=True)
class ExistingPlaylist(ValueObject):
    ref: PlaylistRef


@dataclass(frozen=True, slots=True)
class NewPlaylist(ValueObject):
    platform: Platform
    title: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class LibraryDestination(ValueObject):
    platform: Platform
    account_id: UUID


TrackDestination = ExistingPlaylist | NewPlaylist | LibraryDestination


def destination_platform(destination: TrackDestination) -> Platform:
    if isinstance(destination, ExistingPlaylist):
        return destination.ref.platform
    return destination.platform


def source_platform(source: TrackSource) -> Platform | None:
    if isinstance(source, PlaylistSource):
        return source.ref.platform
    if isinstance(source, LibrarySource):
        return source.platform
    return None  # FileSource — не площадка


@dataclass(frozen=True, slots=True)
class MatchResult(ValueObject):
    """Собственный (упрощённый) слепок matching.domain.TrackMatch для этого контекста —
    transfers не импортирует matching.domain, чтобы не нарушать независимость контекстов
    (import-linter contract "Context independence"); перевод делает application-слой.
    """

    target_ref: ExternalTrackRef
    method: str
    score: MatchScore
    # TrackRestriction.value найденного трека ("preview_only") — пометка для отчёта.
    restriction: str | None = None


class RecognitionState(StrEnum):
    """Где трек в распознавании по звуку (этап 6, ARCHITECTURE.md 11j)."""

    QUEUED = "queued"  # ждёт worker-recognize
    READY = "ready"  # распознавание закончено — ждёт поиска на цели
    FINAL = "final"  # итог применён к треку


# Причины итога распознавания (ItemRecognition.note). Кроме этих — вердикты поиска по
# звуку из matching (exact, altered, text_mismatch, not_on_target), строкой.
NOTE_NO_MATCH = "no_match"  # распознаватели не узнали
NOTE_NO_AUDIO = "no_audio"  # у площадки нет звука трека (шифрование, нет потока)
NOTE_UNAVAILABLE = "unavailable"  # сейчас не получилось (сеть, нет доступа, сбой)
NOTE_TIMEOUT = "timeout"  # не уложились в потолок фазы распознавания переноса
# «Ещё раз по звуку» имеет смысл только после временной неудачи.
RETRYABLE_NOTES = frozenset({NOTE_UNAVAILABLE, NOTE_TIMEOUT})


@dataclass(frozen=True, slots=True)
class ItemRecognition(ValueObject):
    state: RecognitionState
    # Итог текстового поиска (UNCERTAIN | NOT_FOUND) — остаётся, если звук не поможет.
    text_status: "TransferItemStatus"
    note: str | None = None
    # Снимок распознанного (для поиска на цели и для подсказки в ревью).
    heard: RecognizedTrack | None = None

    def __post_init__(self) -> None:
        if self.text_status not in (TransferItemStatus.UNCERTAIN, TransferItemStatus.NOT_FOUND):
            raise ValueError(f"Распознавание после текстового итога {self.text_status}")


@dataclass(frozen=True, slots=True)
class TransferProgress(ValueObject):
    """Счётчики items переноса. В БД — колонки transfers, которые run_match обновляет
    атомарно (`SET matched = matched + 1`), а не пересчитывает из загруженного агрегата:
    параллельные джобы одного переноса не теряют чужие обновления."""

    total: int = 0
    pending: int = 0
    matched: int = 0
    uncertain: int = 0
    not_found: int = 0
    added: int = 0
    failed: int = 0
    skipped: int = 0

    @classmethod
    def from_statuses(cls, statuses: Iterable[TransferItemStatus]) -> "TransferProgress":
        counts = Counter(statuses)
        return cls(
            total=sum(counts.values()),
            pending=counts[TransferItemStatus.PENDING],
            matched=counts[TransferItemStatus.MATCHED],
            uncertain=counts[TransferItemStatus.UNCERTAIN],
            not_found=counts[TransferItemStatus.NOT_FOUND],
            added=counts[TransferItemStatus.ADDED],
            failed=counts[TransferItemStatus.FAILED],
            skipped=counts[TransferItemStatus.SKIPPED],
        )

    def status_after_matching(self) -> TransferStatus | None:
        """Куда переходит перенос, когда последний item сопоставлен; None — ещё рано."""
        if self.pending > 0:
            return None
        if self.uncertain + self.not_found > 0:
            return TransferStatus.REVIEW
        return TransferStatus.WRITING

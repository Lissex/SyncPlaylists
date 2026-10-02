from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from syncplaylists.shared_kernel.domain.base import ValueObject
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

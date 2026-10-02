from dataclasses import dataclass
from enum import StrEnum

from syncplaylists.shared_kernel.domain.base import ValueObject
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    PlaylistRef,
)


@dataclass(frozen=True, slots=True)
class TrackQuery(ValueObject):
    title: str
    artist: str | None = None
    isrc: ISRC | None = None
    duration: Duration | None = None


@dataclass(frozen=True, slots=True)
class TrackCandidate(ValueObject):
    ref: ExternalTrackRef
    title: str
    artist: str
    duration: Duration | None = None
    isrc: ISRC | None = None


class InsertOrder(StrEnum):
    """Порядок, в котором площадка кладёт новые треки в медиатеку/плейлист."""

    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True, slots=True)
class PlaylistSnapshot(ValueObject):
    ref: PlaylistRef
    title: str
    description: str | None
    tracks: tuple[TrackCandidate, ...]


@dataclass(frozen=True, slots=True)
class AddResult(ValueObject):
    added: tuple[ExternalTrackRef, ...]
    failed: tuple[ExternalTrackRef, ...]

from dataclasses import dataclass

from syncplaylists.shared_kernel.domain.base import ValueObject
from syncplaylists.shared_kernel.domain.value_objects import ISRC, Duration, ExternalTrackRef


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

from dataclasses import dataclass
from uuid import UUID

from syncplaylists.shared_kernel.domain.base import Entity
from syncplaylists.shared_kernel.domain.value_objects import ISRC, Duration, Platform


@dataclass(eq=False, slots=True)
class CanonicalTrack(Entity):
    title_norm: str
    artist_norm: str
    duration: Duration | None = None
    isrc: ISRC | None = None
    mbid: str | None = None


@dataclass(eq=False, slots=True)
class PlatformTrack(Entity):
    platform: Platform
    external_id: str
    raw_title: str
    raw_artist: str
    duration: Duration | None = None
    isrc: ISRC | None = None
    canonical_id: UUID | None = None

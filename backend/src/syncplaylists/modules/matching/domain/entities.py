from dataclasses import dataclass
from enum import StrEnum

from syncplaylists.shared_kernel.domain.base import Entity
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, MatchScore, Platform


class MatchMethod(StrEnum):
    CACHE = "cache"
    ISRC = "isrc"
    FUZZY = "fuzzy"
    AUDIO = "audio"  # задел под этап 6, пока не используется
    MANUAL = "manual"


@dataclass(eq=False, slots=True)
class TrackMatch(Entity):
    source_ref: ExternalTrackRef
    target_platform: Platform
    target_ref: ExternalTrackRef
    method: MatchMethod
    score: MatchScore
    confirmations: int = 0

    def confirm(self) -> None:
        self.confirmations += 1

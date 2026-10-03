from dataclasses import dataclass
from enum import StrEnum

from syncplaylists.shared_kernel.domain.base import Entity
from syncplaylists.shared_kernel.domain.search import TrackRestriction
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, MatchScore, Platform


class MatchMethod(StrEnum):
    CACHE = "cache"
    # Источник и назначение на одной площадке: трек и есть сам себе соответствие.
    SAME_PLATFORM = "same_platform"
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
    # Ограничение найденного трека (например, только превью без подписки) — пометка для
    # отчёта; хранится в кэше соответствий, чтобы попадание в кэш её не теряло.
    restriction: TrackRestriction | None = None

    def confirm(self) -> None:
        self.confirmations += 1

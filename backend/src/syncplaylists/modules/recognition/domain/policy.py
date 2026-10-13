from dataclasses import dataclass
from enum import StrEnum

from syncplaylists.shared_kernel.domain.value_objects import Platform


class Ineligibility(StrEnum):
    """Почему трек не отправляется на распознавание."""

    OPTED_OUT = "opted_out"  # перенос без «Распознавать по звуку»
    NO_AUDIO_SOURCE = "no_audio_source"  # площадка не отдаёт звук (VK, Spotify — 11j)
    HAS_ISRC = "has_isrc"  # распознаватель вернул бы тот же ISRC — пустой запрос
    TOO_SHORT = "too_short"
    # sped up / slowed / nightcore: Shazam на изменённой скорости уверенно ошибается (11j).
    SPEED_ALTERED = "speed_altered"


@dataclass(frozen=True, slots=True)
class RecognitionPolicy:
    audio_platforms: frozenset[Platform]
    min_track_ms: int

    def check(
        self,
        *,
        platform: Platform,
        duration_ms: int | None,
        has_isrc: bool,
        speed_altered: bool,
        opted_in: bool = True,
    ) -> Ineligibility | None:
        """None — трек подходит. Длительность неизвестна — подходит: окно с начала."""
        if not opted_in:
            return Ineligibility.OPTED_OUT
        if platform not in self.audio_platforms:
            return Ineligibility.NO_AUDIO_SOURCE
        if has_isrc:
            return Ineligibility.HAS_ISRC
        if speed_altered:
            return Ineligibility.SPEED_ALTERED
        if duration_ms is not None and duration_ms < self.min_track_ms:
            return Ineligibility.TOO_SHORT
        return None

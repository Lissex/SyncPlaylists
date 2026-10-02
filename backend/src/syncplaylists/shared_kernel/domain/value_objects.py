import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final

from syncplaylists.shared_kernel.domain.base import ValueObject

_ISRC_PATTERN: Final = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")


class Platform(StrEnum):
    SPOTIFY = "spotify"
    YANDEX = "yandex"
    VK = "vk"
    SOUNDCLOUD = "soundcloud"
    YTMUSIC = "ytmusic"


@dataclass(frozen=True, slots=True)
class ISRC(ValueObject):
    value: str

    def __post_init__(self) -> None:
        if not _ISRC_PATTERN.match(self.value):
            raise ValueError(f"Некорректный ISRC: {self.value!r}")


@dataclass(frozen=True, slots=True)
class Duration(ValueObject):
    milliseconds: int

    def __post_init__(self) -> None:
        if self.milliseconds < 0:
            raise ValueError(f"Длительность не может быть отрицательной: {self.milliseconds}")

    def is_close_to(self, other: "Duration", tolerance_ms: int = 3000) -> bool:
        return abs(self.milliseconds - other.milliseconds) <= tolerance_ms


@dataclass(frozen=True, slots=True)
class ExternalTrackRef(ValueObject):
    platform: Platform
    external_id: str


@dataclass(frozen=True, slots=True)
class PlaylistRef(ValueObject):
    platform: Platform
    external_id: str


class MatchTier(StrEnum):
    AUTO = "auto"
    UNCERTAIN = "uncertain"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class MatchScore(ValueObject):
    value: float

    AUTO_THRESHOLD: ClassVar[float] = 0.90
    UNCERTAIN_THRESHOLD: ClassVar[float] = 0.70

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError(f"MatchScore должен быть в диапазоне [0, 1]: {self.value}")

    @property
    def tier(self) -> MatchTier:
        if self.value >= self.AUTO_THRESHOLD:
            return MatchTier.AUTO
        if self.value >= self.UNCERTAIN_THRESHOLD:
            return MatchTier.UNCERTAIN
        return MatchTier.NOT_FOUND

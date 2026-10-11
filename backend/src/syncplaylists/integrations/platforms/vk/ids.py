"""Идентификаторы VK (4c-5).

Трек — `"<owner_id>_<audio_id>[_<access_key>]"`: owner отрицательный у релизов
(`-2001…`) и сообществ. access_key нужен, чтобы добавить чужой трек в «Мою музыку».
Плейлист — тот же формат, что у LinkResolver (`shared_kernel/domain/links.py`):
`"<owner_id>_<playlist_id>[_<access_hash>]"`, id отрицательный у сгенерированных."""

import re
from dataclasses import dataclass
from typing import Final, Self

_PATTERN: Final = re.compile(r"^(-?\d{1,20})_(-?\d{1,20})(?:_([A-Za-z0-9]{1,64}))?$")


def _parse(value: str, what: str) -> tuple[int, int, str | None]:
    match = _PATTERN.match(value)
    if match is None:
        raise ValueError(f"не {what} VK: {value!r}")
    return int(match[1]), int(match[2]), match[3]


@dataclass(frozen=True, slots=True)
class VkTrackId:
    owner_id: int
    audio_id: int
    access_key: str | None = None

    @classmethod
    def parse(cls, value: str) -> Self:
        owner_id, audio_id, access_key = _parse(value, "трек")
        return cls(owner_id, audio_id, access_key)

    @property
    def plain(self) -> str:
        """Без access_key — так трек узнаётся в списках и так его принимает editPlaylist."""
        return f"{self.owner_id}_{self.audio_id}"

    def __str__(self) -> str:
        return f"{self.plain}_{self.access_key}" if self.access_key else self.plain


@dataclass(frozen=True, slots=True)
class VkPlaylistId:
    owner_id: int
    playlist_id: int
    access_key: str | None = None

    @classmethod
    def parse(cls, value: str) -> Self:
        owner_id, playlist_id, access_key = _parse(value, "плейлист")
        return cls(owner_id, playlist_id, access_key)

    def __str__(self) -> str:
        plain = f"{self.owner_id}_{self.playlist_id}"
        return f"{plain}_{self.access_key}" if self.access_key else plain

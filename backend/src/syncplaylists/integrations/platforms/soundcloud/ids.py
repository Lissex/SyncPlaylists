"""Форматы идентификаторов SoundCloud в external_id — контракт с LinkResolver
(shared_kernel/domain/links.py) и с тем, что лежит в БД (platform_tracks, transfers)."""

import re
from dataclasses import dataclass
from typing import Final

_TRACK_ID: Final = re.compile(r"^\d+$")
_SET_PATH: Final = re.compile(
    r"^(?P<user>[\w-]+)/sets/(?P<slug>[\w-]+)(?:/(?P<secret>s-[A-Za-z0-9]+))?$"
)
_LIKES_PATH: Final = re.compile(r"^(?P<user>[\w-]+)/likes$")
_NUMERIC: Final = re.compile(r"^(?P<id>\d+)(?::(?P<secret>s-[A-Za-z0-9]+))?$")


def parse_track_id(external_id: str) -> int:
    """Трек — числовой id ("328259811")."""
    if not _TRACK_ID.match(external_id):
        raise ValueError(f"Некорректный id трека SoundCloud: {external_id!r}")
    return int(external_id)


@dataclass(frozen=True, slots=True)
class SoundCloudPlaylistId:
    """Плейлист (сет) — одно из:
    - путь из ссылки "<user>/sets/<slug>[/s-<secret>]" (секрет даёт доступ к приватному
      сету) — резолвится через /resolve;
    - числовой id "<id>[:s-<secret>]" — так храним созданные нами: стабилен при
      переименовании сета, без лишнего /resolve;
    - "<user>/likes" — лайки пользователя (только чтение)."""

    path: str | None = None
    playlist_id: int | None = None
    secret_token: str | None = None
    likes_of: str | None = None

    @classmethod
    def parse(cls, external_id: str) -> "SoundCloudPlaylistId":
        value = external_id.strip().strip("/")
        if match := _SET_PATH.match(value):
            return cls(path=value, secret_token=match["secret"])
        if match := _LIKES_PATH.match(value):
            return cls(likes_of=match["user"])
        if match := _NUMERIC.match(value):
            return cls(playlist_id=int(match["id"]), secret_token=match["secret"])
        raise ValueError(f"Некорректный id плейлиста SoundCloud: {external_id!r}")

    @staticmethod
    def for_created(playlist_id: int, secret_token: str | None) -> str:
        return f"{playlist_id}:{secret_token}" if secret_token else str(playlist_id)

    @property
    def url(self) -> str:
        assert self.path is not None
        return f"https://soundcloud.com/{self.path}"

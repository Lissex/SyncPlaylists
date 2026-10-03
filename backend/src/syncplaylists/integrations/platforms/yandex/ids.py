"""Форматы идентификаторов Яндекс Музыки в external_id — контракт с LinkResolver
(shared_kernel/domain/links.py) и с тем, что лежит в БД (platform_tracks, transfers)."""

import re
from dataclasses import dataclass
from typing import Final

_TRACK_ID: Final = re.compile(r"^(?P<track>\d+)(?::(?P<album>\d+))?$")
_USER_PLAYLIST: Final = re.compile(r"^(?P<owner>[^:/]+):(?P<kind>\d+)$")

# kind «Мне нравится» в users/<login>/playlists/<kind>.
LIKES_PLAYLIST_KIND: Final = 3


@dataclass(frozen=True, slots=True)
class YandexTrackId:
    """Трек — "<track_id>:<album_id>" или просто "<track_id>": у части треков
    (загруженные пользователем, изъятые из альбома) альбома нет. Альбом нужен только для
    вставки в плейлист; один и тот же трек в разных альбомах — тот же трек, поэтому
    дубли считаются по track_id."""

    track_id: str
    album_id: str | None = None

    @classmethod
    def parse(cls, external_id: str) -> "YandexTrackId":
        match = _TRACK_ID.match(external_id.strip())
        if match is None:
            raise ValueError(f"Некорректный id трека Яндекса: {external_id!r}")
        return cls(match["track"], match["album"])

    def __str__(self) -> str:
        return f"{self.track_id}:{self.album_id}" if self.album_id else self.track_id


@dataclass(frozen=True, slots=True)
class YandexPlaylistId:
    """Плейлист — "<owner>:<kind>" (owner — login или uid) или uuid нового формата
    ("lk.…", "ar.…") из ссылок music.yandex.ru/playlists/<uuid>."""

    owner: str | None = None
    kind: int | None = None
    uuid: str | None = None

    @classmethod
    def parse(cls, external_id: str) -> "YandexPlaylistId":
        if match := _USER_PLAYLIST.match(external_id):
            return cls(owner=match["owner"], kind=int(match["kind"]))
        if external_id and ":" not in external_id and "/" not in external_id:
            return cls(uuid=external_id)
        raise ValueError(f"Некорректный id плейлиста Яндекса: {external_id!r}")

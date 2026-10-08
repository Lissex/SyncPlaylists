"""Ответы операций Яндекс Музыки из браузерного расширения (4c-4).

Расширение отдаёт не сырой JSON api.music.yandex.ru, а его проекцию: в странице из
ответа вырезаются только поля, которые читают mapping.py/gateway.py. Здесь та же
проекция строгими схемами (extra="forbid"): лишнее поле (случайно попавший токен, email,
подписка) — ответ не по контракту. Имена полей — как у API (camelCase, через alias):
`model_dump(by_alias=True)` даёт dict, из которого `de_json` yandex-music собирает те же
модели, что на серверном пути, — маппинг не меняется."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_Text = Annotated[str, Field(max_length=500)]
_LongText = Annotated[str, Field(max_length=5000)]
_Url = Annotated[str, Field(max_length=2000)]
# id трека и альбома: цифры у каталога; у загруженных пользователем бывают и буквы.
_Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9-]{1,64}$")]
_Uid = Annotated[str, Field(pattern=r"^\d{1,20}$")]
_Count = Annotated[int, Field(ge=0)]
_Stamp = Annotated[str, Field(max_length=40)]

# Страница коротких записей плейлиста или «Мне нравится»: ≤ 5000 на задачу (~250 КБ),
# одна задача = одна страница (11h).
MAX_PAGE: int = 5000
# Полных треков за задачу: столько же берёт /tracks за запрос (batch_size шлюза).
MAX_TRACKS: int = 100


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel)


class YmArtist(_Strict):
    id: _Id | None = None
    name: _Text | None = None


class YmAlbum(_Strict):
    id: _Id


class YmTrack(_Strict):
    id: _Id
    title: _Text | None = None
    version: _Text | None = None
    available: bool | None = None
    duration_ms: _Count | None = None
    cover_uri: _Url | None = None
    og_image: _Url | None = None
    artists: Annotated[list[YmArtist], Field(max_length=50)] = Field(default_factory=list)
    albums: Annotated[list[YmAlbum], Field(max_length=50)] = Field(default_factory=list)


class YmTrackShort(_Strict):
    """Запись плейлиста/лайка: id и альбом. `timestamp` нужен конструктору TrackShort."""

    id: _Id
    album_id: _Id | None = None
    timestamp: _Stamp = ""


class YmOwner(_Strict):
    uid: _Uid | None = None
    login: _Text | None = None


class YmPlaylistPage(_Strict):
    """Заголовок плейлиста и страница его записей; next_offset — начало следующей."""

    kind: _Count
    uid: _Uid | None = None
    owner: YmOwner | None = None
    title: _Text | None = None
    description: _LongText | None = None
    revision: _Count
    track_count: _Count
    tracks: Annotated[list[YmTrackShort], Field(max_length=MAX_PAGE)]
    next_offset: _Count | None = None


class YmLibraryPage(_Strict):
    revision: _Count
    tracks: Annotated[list[YmTrackShort], Field(max_length=MAX_PAGE)]
    next_offset: _Count | None = None


class YmTracks(_Strict):
    tracks: Annotated[list[YmTrack], Field(max_length=MAX_TRACKS)]


class YmSearch(_Strict):
    tracks: Annotated[list[YmTrack], Field(max_length=MAX_TRACKS)]


class YmAccount(_Strict):
    """whoami — та же форма, что у SoundCloud (её читает и само расширение для
    connect_platform): id — uid аккаунта, username — отображаемое имя."""

    id: _Uid
    username: _Text | None = None
    login: _Text | None = None


class YmCreated(_Strict):
    kind: _Count
    revision: _Count | None = None


class YmChanged(_Strict):
    revision: _Count
    track_count: _Count | None = None


class YmDone(_Strict):
    """Запись без нужного серверу тела ответа (описание, лайки)."""

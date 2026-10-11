"""Ответы операций VK из браузерного расширения (4c-5).

Расширение отдаёт не сырой ответ web.api.vk.ru, а проекцию: в странице из ответа
вырезаются только поля, которые читают mapping.py/gateway.py. Здесь та же проекция
строгими схемами (extra="forbid"). Ссылки на аудио (`url` трека) в проекции нет и быть не
может: лишнее поле — ответ не по контракту. Токен страницы VK не покидает её вовсе."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

_Text = Annotated[str, Field(max_length=500)]
_LongText = Annotated[str, Field(max_length=5000)]
_Url = Annotated[str, Field(max_length=2000)]
_Id = Annotated[int, Field(ge=-(10**19), le=10**19)]
_Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,64}$")]
_Uid = Annotated[str, Field(pattern=r"^\d{1,20}$")]
_Ref = Annotated[str, Field(pattern=r"^-?\d{1,20}_-?\d{1,20}$")]
_Count = Annotated[int, Field(ge=0)]

# Треков на страницу плейлиста/медиатеки: детали догружаются в странице по 100 за запрос.
MAX_PAGE: int = 500
# Треков за один audio.getById — столько же берёт задача `tracks`.
MAX_TRACKS: int = 100
MAX_SEARCH: int = 50


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VkTrack(_Strict):
    owner_id: _Id
    id: _Id
    access_key: _Key | None = None
    artist: _Text = ""
    title: _Text = ""
    subtitle: _Text | None = None
    duration: _Count | None = None  # секунды
    album_title: _Text | None = None
    cover_url: _Url | None = None
    main_artists: Annotated[list[_Text], Field(max_length=50)] = Field(default_factory=list)
    featured_artists: Annotated[list[_Text], Field(max_length=50)] = Field(default_factory=list)
    # Оригинал релиза, если трек — его копия или перезалив с привязкой к релизу.
    release_audio_id: _Ref | None = None

    @property
    def plain(self) -> str:
        return f"{self.owner_id}_{self.id}"


class VkPlaylist(_Strict):
    owner_id: _Id
    id: _Id
    access_key: _Key | None = None
    title: _Text = ""
    description: _LongText | None = None
    count: _Count = 0
    type: Annotated[str, Field(max_length=20)] | None = None  # ugc | album | generated
    editable: bool = False  # permissions.edit


class VkAccount(_Strict):
    """whoami — та же форма, что у других площадок (её читает и само расширение для
    connect_platform): id — id пользователя VK, username — имя и фамилия."""

    id: _Uid
    username: _Text | None = None


class VkSearch(_Strict):
    tracks: Annotated[list[VkTrack], Field(max_length=MAX_SEARCH)]


class VkTracks(_Strict):
    tracks: Annotated[list[VkTrack], Field(max_length=MAX_TRACKS)]


class VkPlaylistInfo(_Strict):
    playlist: VkPlaylist


class VkPlaylistPage(_Strict):
    """Шапка плейлиста и страница его треков; next_offset — начало следующей."""

    playlist: VkPlaylist
    total: _Count
    tracks: Annotated[list[VkTrack], Field(max_length=MAX_PAGE)]
    next_offset: _Count | None = None


class VkLibraryPage(_Strict):
    total: _Count
    tracks: Annotated[list[VkTrack], Field(max_length=MAX_PAGE)]
    next_offset: _Count | None = None


class VkCreated(_Strict):
    owner_id: _Id
    id: _Id
    access_key: _Key | None = None


class VkAdded(_Strict):
    """audio.add: копия трека в «Моей музыке» пользователя."""

    owner_id: _Id
    id: _Id


class VkDone(_Strict):
    """Запись без нужного серверу тела ответа."""

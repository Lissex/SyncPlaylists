"""Ответы операций SoundCloud из браузерного расширения (4c-3).

Расширение отдаёт не сырой JSON api-v2, а его проекцию: в странице из ответа
вырезаются только поля, которые читает mapping.py/gateway.py, остальное до сервера не
доходит. Здесь — та же проекция строгими схемами (extra="forbid"): лишнее поле, в том
числе случайно попавший токен или cookie, — ответ не по контракту. Шлюзу отдаётся
`model_dump(exclude_none=True, by_alias=True)` — dict той же формы, что у V2Api, поэтому
логика SoundCloudGateway и маппинг не меняются.

Секрет приватного сета на проводе называется `secret` (у SoundCloud — `secret_token`):
в протоколе расширения ключи с «token» запрещены целиком (тест no-secrets), а это не
секрет авторизации, а часть ссылки на сет."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

_Text = Annotated[str, Field(max_length=500)]
_LongText = Annotated[str, Field(max_length=5000)]
_Url = Annotated[str, Field(max_length=2000)]
_Cursor = Annotated[str, Field(max_length=1000)]
_Id = Annotated[int, Field(ge=0)]
_Secret = Annotated[str, Field(max_length=100)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScUser(_Strict):
    id: _Id | None = None
    username: _Text | None = None
    permalink: _Text | None = None
    verified: bool | None = None
    avatar_url: _Url | None = None


class ScPublisherMetadata(_Strict):
    artist: _Text | None = None
    isrc: Annotated[str, Field(max_length=40)] | None = None


class ScTrack(_Strict):
    """Полный трек или заглушка сета `{id, kind, policy}` (без title — её догружают)."""

    id: _Id
    kind: Literal["track"] | None = None
    title: _Text | None = None
    duration: _Id | None = None
    full_duration: _Id | None = None
    policy: Annotated[str, Field(max_length=20)] | None = None
    artwork_url: _Url | None = None
    user: ScUser | None = None
    publisher_metadata: ScPublisherMetadata | None = None


class ScPlaylist(_Strict):
    id: _Id
    kind: Literal["playlist"] | None = None
    title: _Text | None = None
    description: _LongText | None = None
    user_id: _Id | None = None
    secret: _Secret | None = Field(default=None, serialization_alias="secret_token")
    track_count: _Id | None = None
    tracks: Annotated[list[ScTrack], Field(max_length=1000)] = Field(default_factory=list)


class ScMe(_Strict):
    id: _Id
    username: _Text | None = None
    permalink: _Text | None = None
    likes_count: _Id | None = None


class ScLikedTracksPage(_Strict):
    tracks: Annotated[list[ScTrack], Field(max_length=500)]
    next_cursor: _Cursor | None = None


class ScLikedIdsPage(_Strict):
    ids: Annotated[list[_Id], Field(max_length=5000)]
    next_cursor: _Cursor | None = None


class ScTracks(_Strict):
    tracks: Annotated[list[ScTrack], Field(max_length=100)]


class ScCreated(_Strict):
    id: _Id
    secret: _Secret | None = Field(default=None, serialization_alias="secret_token")


class ScDone(_Strict):
    """Запись без тела ответа (лайк, замена треков сета)."""

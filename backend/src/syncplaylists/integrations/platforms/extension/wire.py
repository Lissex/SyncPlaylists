"""Формы ответов расширения по операциям. Всё — extra="forbid": расширение отдаёт
только перечисленные поля, всё лишнее (в том числе случайно попавшие токены/cookie)
отвергается, а не сохраняется (ARCHITECTURE.md, 11h)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackRestriction
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    Platform,
)

_Id = Annotated[str, Field(min_length=1, max_length=200)]
_Text = Annotated[str, Field(max_length=500)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WireTrack(_Strict):
    id: _Id
    title: _Text
    artist: _Text
    artists: list[_Text] = Field(default_factory=list, max_length=50)
    duration_ms: int | None = Field(default=None, ge=0)
    isrc: str | None = None
    cover_url: Annotated[str, Field(max_length=2000)] | None = None
    uploader: _Text | None = None
    rights_holder: bool = False
    restriction: TrackRestriction | None = None

    def to_domain(self, platform: Platform) -> TrackCandidate:
        isrc: ISRC | None = None
        if self.isrc:
            try:
                isrc = ISRC(self.isrc.upper())
            except ValueError:
                isrc = None  # невалидный ISRC площадки — как у адаптеров: отбрасываем
        return TrackCandidate(
            ref=ExternalTrackRef(platform, self.id),
            title=self.title,
            artist=self.artist,
            duration=Duration(self.duration_ms) if self.duration_ms is not None else None,
            isrc=isrc,
            artists=tuple(self.artists),
            cover_url=self.cover_url,
            uploader=self.uploader,
            rights_holder=self.rights_holder,
            restriction=self.restriction,
        )


_Tracks = Annotated[list[WireTrack], Field(max_length=1000)]


class TracksResult(_Strict):
    tracks: _Tracks


class PlaylistInfoResult(_Strict):
    title: _Text
    description: Annotated[str, Field(max_length=5000)] | None = None
    owner_external_id: _Id | None = None
    track_count: int | None = Field(default=None, ge=0)


class PageResult(_Strict):
    """Одна страница длинного чтения: next_cursor None — страница последняя."""

    tracks: _Tracks
    next_cursor: Annotated[str, Field(max_length=500)] | None = None
    # Для первой страницы плейлиста — его шапка.
    title: _Text | None = None
    description: Annotated[str, Field(max_length=5000)] | None = None


class OwnLibraryResult(_Strict):
    own: bool


class CreatedPlaylistResult(_Strict):
    playlist_id: _Id


class AddResultWire(_Strict):
    added: Annotated[list[_Id], Field(max_length=10000)]
    failed: Annotated[list[_Id], Field(max_length=10000)]

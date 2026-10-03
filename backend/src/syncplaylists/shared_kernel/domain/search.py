from dataclasses import dataclass
from enum import StrEnum

from syncplaylists.shared_kernel.domain.base import ValueObject
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    PlaylistRef,
)


@dataclass(frozen=True, slots=True)
class TrackQuery(ValueObject):
    title: str
    artist: str | None = None
    isrc: ISRC | None = None
    duration: Duration | None = None


class TrackRestriction(StrEnum):
    """Ограничение доступности трека на площадке — для пометки в отчёте переноса."""

    # Без подписки слышно только превью (SoundCloud Go+: policy=SNIP).
    PREVIEW_ONLY = "preview_only"


@dataclass(frozen=True, slots=True)
class TrackCandidate(ValueObject):
    ref: ExternalTrackRef
    title: str
    artist: str
    duration: Duration | None = None
    isrc: ISRC | None = None
    # artist — все артисты одной строкой через ", " (её разбирает matching);
    # artists — тот же список по отдельности, если площадка его отдаёт.
    artists: tuple[str, ...] = ()
    cover_url: str | None = None
    # Кто залил трек — у площадок с пользовательскими заливками (SoundCloud); иначе None.
    uploader: str | None = None
    # Заливка от правообладателя (лейбл/подтверждённый артист) — matching предпочитает
    # её перезаливам с тем же названием.
    rights_holder: bool = False
    restriction: TrackRestriction | None = None


class InsertOrder(StrEnum):
    """Порядок, в котором площадка кладёт новые треки в медиатеку/плейлист."""

    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True, slots=True)
class PlaylistSnapshot(ValueObject):
    ref: PlaylistRef
    title: str
    description: str | None
    tracks: tuple[TrackCandidate, ...]


@dataclass(frozen=True, slots=True)
class PlaylistInfo(ValueObject):
    """Шапка плейлиста без треков — дешёвый запрос: владелец (для проверки права
    записи), название, число треков (для предпросмотра ссылки)."""

    ref: PlaylistRef
    title: str
    description: str | None
    owner_external_id: str | None
    track_count: int | None


@dataclass(frozen=True, slots=True)
class AddResult(ValueObject):
    added: tuple[ExternalTrackRef, ...]
    failed: tuple[ExternalTrackRef, ...]

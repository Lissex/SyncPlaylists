from typing import Any, Final

from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import Duration, ExternalTrackRef, Platform

COVER_SIZE: Final = "400x400"


def track_title(title: str, version: str | None) -> str:
    """Яндекс хранит версию отдельным полем ("Live", "Remix by X", "Acoustic Version").
    Склеиваем её в title в скобках — matching извлекает версию именно оттуда
    (matching/domain/version.py) и сравнивает с версией кандидата."""
    if version and version.strip() and version.strip().lower() not in title.lower():
        return f"{title} ({version.strip()})"
    return title


def cover_url(cover_uri: str | None) -> str | None:
    # cover_uri приходит без схемы и с плейсхолдером размера: "avatars.yandex.net/get-…/%%".
    if not cover_uri:
        return None
    return f"https://{cover_uri.replace('%%', COVER_SIZE)}"


def to_candidate(track: Any) -> TrackCandidate | None:
    """yandex_music.Track → TrackCandidate. None — трек недоступен (изъят, гео), его
    нельзя ни добавить, ни послушать; такие пропускаем."""
    if track is None or track.available is False or not track.title:
        return None
    artists = tuple(artist.name for artist in (track.artists or []) if artist.name)
    duration_ms = track.duration_ms
    return TrackCandidate(
        # track_id библиотеки: "<id>:<album_id>" или "<id>" без альбома — тот же
        # формат, что YandexTrackId.
        ref=ExternalTrackRef(Platform.YANDEX, str(track.track_id)),
        title=track_title(track.title, track.version),
        artist=", ".join(artists),
        duration=Duration(duration_ms) if duration_ms is not None and duration_ms >= 0 else None,
        artists=artists,
        cover_url=cover_url(track.cover_uri or track.og_image),
    )

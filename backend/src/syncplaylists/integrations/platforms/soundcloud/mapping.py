"""Трек SoundCloud (JSON api-v2 / официального API) → TrackCandidate.

Формы полей сверены с ответами api-v2 2026-10-03 (search/tracks, resolve, tracks?ids).
"""

import re
from typing import Any, Final

from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackRestriction
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    Platform,
)

# artwork_url приходит в размере "-large" (100×100); "-t500x500" — тот же файл крупнее.
_ARTWORK_SIZE: Final = re.compile(r"-large(?=\.\w+$)")
# policy=SNIP — без SoundCloud Go+ доступно только 30-секундное превью; BLOCK — трек
# недоступен в регионе запроса.
_POLICY_SNIP: Final = "SNIP"
_POLICY_BLOCK: Final = "BLOCK"


def is_full_track(data: Any) -> bool:
    """В сете полными приходят только первые ~5 треков, остальные — заглушки {id, kind,
    policy, monetization_model}: их надо догрузить через /tracks?ids=."""
    return isinstance(data, dict) and bool(data.get("title"))


def is_blocked(data: dict[str, Any]) -> bool:
    return data.get("policy") == _POLICY_BLOCK


def cover_url(data: dict[str, Any]) -> str | None:
    url = data.get("artwork_url") or (data.get("user") or {}).get("avatar_url")
    if not isinstance(url, str) or not url:
        return None
    return _ARTWORK_SIZE.sub("-t500x500", url)


def _isrc(metadata: dict[str, Any]) -> ISRC | None:
    raw = metadata.get("isrc")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return ISRC(raw.strip().upper().replace("-", ""))
    except ValueError:
        return None  # лейблы иногда пишут туда что попало — без ISRC, но не падаем


def to_candidate(data: Any) -> TrackCandidate | None:
    """None — не трек или заглушка без названия (удалён/недоступен)."""
    if not is_full_track(data) or data.get("id") is None:
        return None
    metadata = data.get("publisher_metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    user = data.get("user") or {}
    uploader = user.get("username") if isinstance(user, dict) else None
    uploader = uploader.strip() if isinstance(uploader, str) and uploader.strip() else None

    # Артист: из метаданных лейбла, если есть, иначе заливщик. «Artist - Title» в самом
    # названии разберёт TrackNormalizer — это частый случай у перезаливов.
    publisher_artist = metadata.get("artist")
    if isinstance(publisher_artist, str) and publisher_artist.strip():
        artist = publisher_artist.strip()
    else:
        artist = uploader or ""
    isrc = _isrc(metadata)
    rights_holder = bool(isrc) or bool(isinstance(publisher_artist, str) and publisher_artist)
    if isinstance(user, dict) and user.get("verified") is True:
        rights_holder = True

    # У SNIP duration — длина превью (30 с), full_duration — настоящая.
    duration_ms = data.get("full_duration") or data.get("duration")
    restriction = TrackRestriction.PREVIEW_ONLY if data.get("policy") == _POLICY_SNIP else None
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.SOUNDCLOUD, str(data["id"])),
        title=str(data["title"]).strip(),
        artist=artist,
        duration=Duration(int(duration_ms)) if isinstance(duration_ms, int) else None,
        isrc=isrc,
        artists=(artist,) if artist else (),
        cover_url=cover_url(data),
        uploader=uploader,
        rights_holder=rights_holder,
        restriction=restriction,
    )

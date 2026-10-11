import re
import unicodedata
from typing import Final

from syncplaylists.integrations.platforms.vk.extension_wire import VkTrack
from syncplaylists.integrations.platforms.vk.ids import VkTrackId
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import Duration, ExternalTrackRef, Platform

_SPACES: Final = re.compile(r"\s+")


def track_title(title: str, subtitle: str | None) -> str:
    """Версию VK держит в subtitle ("Live", "Remix") — склеиваем её в title в скобках, как
    у Яндекса: matching извлекает версию оттуда (matching/domain/version.py)."""
    if subtitle and subtitle.strip() and subtitle.strip().lower() not in title.lower():
        return f"{title} ({subtitle.strip()})"
    return title


def is_official(track: VkTrack) -> bool:
    """Официальный релиз (спайк 4c-5-0): есть и main_artists, и release_audio_id.
    is_licensed не различает ничего — он true и у «(Unreleased)» и «[V3]»."""
    return bool(track.main_artists) and track.release_audio_id is not None


def to_candidate(track: VkTrack) -> TrackCandidate | None:
    if not track.title or not (track.artist or track.main_artists):
        return None
    artists = tuple(name for name in (*track.main_artists, *track.featured_artists) if name)
    track_id = VkTrackId(track.owner_id, track.id, track.access_key)
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.VK, str(track_id)),
        title=track_title(track.title, track.subtitle),
        # Строка артистов VK («X feat. Y») — если нет отдельных main/featured.
        artist=", ".join(artists) if artists else track.artist,
        duration=Duration(track.duration * 1000) if track.duration is not None else None,
        artists=artists,
        cover_url=track.cover_url,
        rights_holder=is_official(track),
    )


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    return _SPACES.sub(" ", folded).strip()


def identity(track: VkTrack) -> set[str]:
    """Ключи «это тот же трек» для отсева уже добавленного. В плейлист и в «Мою музыку»
    VK кладёт копии с новыми id, поэтому одного id мало: копия узнаётся по оригиналу
    релиза (release_audio_id) или по исполнителю, названию и длительности — у копии они
    те же, что у трека, с которого она сделана."""
    keys = {f"id:{track.plain}"}
    if track.release_audio_id:
        keys.add(f"id:{track.release_audio_id}")
    if track.duration is not None:
        keys.add(f"text:{_norm(track.artist)}|{_norm(track.title)}|{track.duration}")
    return keys

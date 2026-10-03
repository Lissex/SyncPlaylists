"""Разбор ссылок на плейлисты всех площадок → PlaylistRef / медиатека.

Чистые функции на stdlib (re + urllib.parse): сетевой только раскрыватель коротких
ссылок, он за портом UrlExpander. Формат external_id каждой площадки — контракт с её
адаптером (см. комментарии у парсеров).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qs, unquote, urlsplit

from syncplaylists.shared_kernel.domain.base import ValueObject
from syncplaylists.shared_kernel.domain.errors import UnsupportedLinkError
from syncplaylists.shared_kernel.domain.ports import UrlExpander
from syncplaylists.shared_kernel.domain.value_objects import Platform, PlaylistRef

MAX_LINK_LENGTH: Final = 2048


@dataclass(frozen=True, slots=True)
class PlaylistLink(ValueObject):
    ref: PlaylistRef


@dataclass(frozen=True, slots=True)
class LibraryLink(ValueObject):
    """Ссылка на медиатеку того, кто её открывает (Spotify /collection/tracks,
    YouTube Music LM) — однозначно «своя»."""

    platform: Platform


ResolvedLink = PlaylistLink | LibraryLink

_URL_IN_TEXT: Final = re.compile(r"(?:https?://|spotify:)\S+", re.IGNORECASE)
_BARE_HOST: Final = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}(?:/|$)", re.IGNORECASE)


def _not_a_playlist() -> UnsupportedLinkError:
    return UnsupportedLinkError("not_a_playlist", "Ссылка ведёт не на плейлист")


# --- Яндекс Музыка -------------------------------------------------------------------
# external_id: "<login>:<kind>" (users/<login>/playlists/<kind>) или uuid нового формата
# из music.yandex.ru/playlists/<uuid> — как есть, без префикса (так его принимает API).

_YANDEX_HOSTS: Final = re.compile(r"^(?:next\.)?music\.yandex\.(?:ru|com|by|kz|uz)$")
_YANDEX_USER_PLAYLIST: Final = re.compile(r"^/users/(?P<login>[^/]+)/playlists/(?P<kind>\d+)/?$")
_YANDEX_UUID_PLAYLIST: Final = re.compile(r"^/playlists/(?P<uuid>[A-Za-z0-9._-]+)/?$")


def _parse_yandex(path: str, query: dict[str, list[str]]) -> ResolvedLink:
    if match := _YANDEX_USER_PLAYLIST.match(path):
        login = unquote(match["login"])
        return PlaylistLink(PlaylistRef(Platform.YANDEX, f"{login}:{match['kind']}"))
    if match := _YANDEX_UUID_PLAYLIST.match(path):
        return PlaylistLink(PlaylistRef(Platform.YANDEX, match["uuid"]))
    raise _not_a_playlist()


# --- Spotify -------------------------------------------------------------------------
# external_id: base62-id плейлиста (22 символа).

_SPOTIFY_HOSTS: Final = re.compile(r"^(?:open|play)\.spotify\.com$")
_SPOTIFY_ID: Final = r"[A-Za-z0-9]{22}"
_SPOTIFY_PLAYLIST: Final = re.compile(
    rf"^(?:/intl-[a-z]{{2}}(?:-[a-z]{{2}})?)?(?:/user/[^/]+)?/playlist/(?P<id>{_SPOTIFY_ID})/?$",
    re.IGNORECASE,
)
_SPOTIFY_LIBRARY: Final = re.compile(r"^(?:/intl-[a-z-]+)?/collection/tracks/?$", re.IGNORECASE)
_SPOTIFY_URI: Final = re.compile(rf"^spotify:(?:user:[^:]+:)?playlist:(?P<id>{_SPOTIFY_ID})$")


def _parse_spotify(path: str, query: dict[str, list[str]]) -> ResolvedLink:
    if match := _SPOTIFY_PLAYLIST.match(path):
        return PlaylistLink(PlaylistRef(Platform.SPOTIFY, match["id"]))
    if _SPOTIFY_LIBRARY.match(path):
        return LibraryLink(Platform.SPOTIFY)
    raise _not_a_playlist()


# --- VK ------------------------------------------------------------------------------
# external_id: "<owner_id>_<playlist_id>[_<access_hash>]" — owner у сообществ отрицательный.

_VK_HOSTS: Final = re.compile(r"^(?:m\.|www\.)?vk\.(?:com|ru)$")
_VK_ID: Final = r"(?P<owner>-?\d+)_(?P<id>\d+)"
_VK_PATH_PLAYLIST: Final = re.compile(
    rf"^/(?:music/(?:playlist|album)/|audio_playlist){_VK_ID}(?:_(?P<hash>[0-9a-f]+))?/?$"
)
_VK_Z_PLAYLIST: Final = re.compile(rf"^audio_playlist{_VK_ID}(?:/(?P<hash>[0-9a-f]+))?$")


def _vk_ref(owner: str, playlist_id: str, access_hash: str | None) -> PlaylistLink:
    parts = [owner, playlist_id] + ([access_hash] if access_hash else [])
    return PlaylistLink(PlaylistRef(Platform.VK, "_".join(parts)))


def _parse_vk(path: str, query: dict[str, list[str]]) -> ResolvedLink:
    if match := _VK_PATH_PLAYLIST.match(path):
        return _vk_ref(match["owner"], match["id"], match["hash"])
    # Плейлист, открытый поверх другой страницы: ?z=audio_playlist<owner>_<id>/<hash>
    for z in query.get("z", []):
        if match := _VK_Z_PLAYLIST.match(unquote(z)):
            return _vk_ref(match["owner"], match["id"], match["hash"])
    # Мобильная версия: /audio?act=audio_playlist<owner>_<id>&access_hash=<hash>
    for act in query.get("act", []):
        if match := _VK_Z_PLAYLIST.match(act):
            access_hash = (query.get("access_hash") or [match["hash"]])[0]
            return _vk_ref(match["owner"], match["id"], access_hash)
    raise _not_a_playlist()


# --- SoundCloud ----------------------------------------------------------------------
# external_id: путь "<user>/sets/<slug>[/s-<secret>]" — адаптер резолвит его через
# /resolve API; "<user>/likes" — лайки конкретного пользователя (свои ли — решает адаптер).

_SOUNDCLOUD_HOSTS: Final = re.compile(r"^(?:m\.|www\.)?soundcloud\.com$")
_SOUNDCLOUD_SET: Final = re.compile(
    r"^/(?P<user>[\w-]+)/sets/(?P<slug>[\w-]+)(?:/(?P<secret>s-[A-Za-z0-9]+))?/?$"
)
_SOUNDCLOUD_LIKES: Final = re.compile(r"^/(?P<user>[\w-]+)/likes/?$")
_SOUNDCLOUD_RESERVED: Final = frozenset(
    {"discover", "search", "stream", "charts", "pages", "upload"}
)


def _parse_soundcloud(path: str, query: dict[str, list[str]]) -> ResolvedLink:
    if match := _SOUNDCLOUD_SET.match(path):
        external_id = f"{match['user']}/sets/{match['slug']}"
        if match["secret"]:
            external_id += f"/{match['secret']}"
        return PlaylistLink(PlaylistRef(Platform.SOUNDCLOUD, external_id))
    if (match := _SOUNDCLOUD_LIKES.match(path)) and match["user"] not in _SOUNDCLOUD_RESERVED:
        if match["user"] == "you":
            return LibraryLink(Platform.SOUNDCLOUD)
        return PlaylistLink(PlaylistRef(Platform.SOUNDCLOUD, f"{match['user']}/likes"))
    raise _not_a_playlist()


# --- YouTube / YouTube Music ---------------------------------------------------------
# external_id: id плейлиста без префикса "VL" (PL…, OLAK5uy_…). LM — «Понравившиеся».

_YOUTUBE_HOSTS: Final = re.compile(r"^(?:music\.|www\.|m\.)?youtube\.com$|^youtu\.be$")
_YOUTUBE_LIST_ID: Final = re.compile(r"^[A-Za-z0-9_-]+$")
_YOUTUBE_BROWSE: Final = re.compile(r"^/browse/(?P<id>VL[A-Za-z0-9_-]+)/?$")


def _parse_youtube(path: str, query: dict[str, list[str]]) -> ResolvedLink:
    list_ids = query.get("list", [])
    if not list_ids and (match := _YOUTUBE_BROWSE.match(path)):
        list_ids = [match["id"]]
    if not list_ids or not _YOUTUBE_LIST_ID.match(list_ids[0]):
        raise _not_a_playlist()
    list_id = list_ids[0]
    if list_id.startswith("VL"):
        list_id = list_id[2:]
    if list_id == "LM":
        return LibraryLink(Platform.YTMUSIC)
    if list_id == "LL":
        raise UnsupportedLinkError(
            "not_a_playlist", "«Понравившиеся видео» — не музыкальный плейлист"
        )
    if list_id.startswith("RD"):
        raise UnsupportedLinkError(
            "mix_not_supported", "Автоматические миксы YouTube не переносятся"
        )
    return PlaylistLink(PlaylistRef(Platform.YTMUSIC, list_id))


_Parser = Callable[[str, dict[str, list[str]]], ResolvedLink]
_PARSERS: Final[tuple[tuple[re.Pattern[str], _Parser], ...]] = (
    (_YANDEX_HOSTS, _parse_yandex),
    (_SPOTIFY_HOSTS, _parse_spotify),
    (_VK_HOSTS, _parse_vk),
    (_SOUNDCLOUD_HOSTS, _parse_soundcloud),
    (_YOUTUBE_HOSTS, _parse_youtube),
)


def extract_url(raw: str) -> str:
    """Ссылка из того, что вставил пользователь: часто это текст «Слушай … https://…»
    из кнопки «Поделиться» или ссылка без схемы."""
    text = raw.strip()
    if len(text) > MAX_LINK_LENGTH:
        raise UnsupportedLinkError("too_long", "Слишком длинная ссылка")
    if match := _URL_IN_TEXT.search(text):
        return match.group(0).rstrip(".,;)»\"'")
    if _BARE_HOST.match(text):
        return f"https://{text}"
    raise UnsupportedLinkError("invalid_url", "Это не ссылка")


def link_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def parse_link(url: str) -> ResolvedLink:
    """Разбор уже полной (не короткой) ссылки площадки. Бросает UnsupportedLinkError."""
    if match := _SPOTIFY_URI.match(url):
        return PlaylistLink(PlaylistRef(Platform.SPOTIFY, match["id"]))
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsupportedLinkError("invalid_url", "Это не ссылка") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise UnsupportedLinkError("invalid_url", "Поддерживаются только http(s)-ссылки")
    host = (parts.hostname or "").lower()
    query = parse_qs(parts.query)
    path = parts.path or "/"
    for hosts, parser in _PARSERS:
        if hosts.match(host):
            return parser(path, query)
    raise UnsupportedLinkError("unknown_host", f"Ссылка не на поддерживаемую площадку: {host}")


class LinkResolver:
    """Доменный сервис: сырая ссылка/текст → плейлист или медиатека площадки.
    Короткие ссылки раскрываются через UrlExpander. Поддерживается ли площадка
    адаптерами — решает вызывающий application-слой, а не резолвер."""

    def __init__(self, expander: UrlExpander) -> None:
        self._expander = expander

    async def resolve(self, raw: str) -> ResolvedLink:
        url = extract_url(raw)
        if self._expander.is_short_link(link_host(url)):
            url = await self._expander.expand(url)
        return parse_link(url)

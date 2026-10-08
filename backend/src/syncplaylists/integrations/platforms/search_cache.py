"""Кэш результатов поиска площадки — меньше запросов к её квоте.

Повторный перенос того же плейлиста, популярные треки у разных пользователей и повтор
run_match после 429 дают те же поисковые запросы: результат берётся из кэша. Это
публичные данные каталога, не пользовательские, поэтому кэш общий на всех.
"""

import hashlib
import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, Final, Protocol

from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.search import (
    AddResult,
    InsertOrder,
    PlaylistInfo,
    PlaylistSnapshot,
    TrackCandidate,
    TrackQuery,
    TrackRestriction,
)
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)

logger = logging.getLogger(__name__)

# «Ничего не нашлось» могло быть временным (трек добавят в каталог) — держим меньше.
_EMPTY_RESULT_MAX_TTL_SECONDS: Final = 3600


class TextCache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


def _candidate_to_dict(candidate: TrackCandidate) -> dict[str, Any]:
    return {
        "platform": candidate.ref.platform.value,
        "external_id": candidate.ref.external_id,
        "title": candidate.title,
        "artist": candidate.artist,
        "duration_ms": candidate.duration.milliseconds if candidate.duration else None,
        "isrc": candidate.isrc.value if candidate.isrc else None,
        "artists": list(candidate.artists),
        "cover_url": candidate.cover_url,
        "uploader": candidate.uploader,
        "rights_holder": candidate.rights_holder,
        "restriction": candidate.restriction.value if candidate.restriction else None,
    }


def _candidate_from_dict(data: dict[str, Any]) -> TrackCandidate:
    return TrackCandidate(
        ref=ExternalTrackRef(Platform(data["platform"]), data["external_id"]),
        title=data["title"],
        artist=data["artist"],
        duration=Duration(data["duration_ms"]) if data.get("duration_ms") is not None else None,
        isrc=ISRC(data["isrc"]) if data.get("isrc") else None,
        artists=tuple(data.get("artists") or ()),
        cover_url=data.get("cover_url"),
        uploader=data.get("uploader"),
        rights_holder=bool(data.get("rights_holder", False)),
        restriction=TrackRestriction(data["restriction"]) if data.get("restriction") else None,
    )


class CachedSearchGateway:
    """Декоратор MusicPlatformGateway: search/search_by_isrc через кэш, всё остальное —
    как есть. Сбой кэша (Redis недоступен, битое значение) не ломает поиск: идём в
    площадку напрямую."""

    def __init__(self, inner: MusicPlatformGateway, cache: TextCache, ttl_seconds: int) -> None:
        self._inner = inner
        self._cache = cache
        self._ttl = ttl_seconds
        self.platform = inner.platform

    # --- кэшируемое ---------------------------------------------------------------------

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        # Нормализуем то, что влияет на поисковый запрос: регистр и пробелы по краям.
        # Длительность в запрос площадки не входит — результаты сравнит скорер.
        parts = (
            query.title.strip().lower(),
            (query.artist or "").strip().lower(),
            query.isrc.value if query.isrc else "",
            str(limit),
        )
        key = self._key("q", "\x1f".join(parts))
        cached = await self._read(key)
        if cached is not None:
            return cached
        result = await self._inner.search(query, limit)
        await self._write(key, result)
        return result

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]:
        key = self._key("isrc", isrc.value)
        cached = await self._read(key)
        if cached is not None:
            return cached
        result = await self._inner.search_by_isrc(isrc)
        await self._write(key, result)
        return result

    # --- без изменений ------------------------------------------------------------------

    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot:
        return await self._inner.get_playlist(ref)

    async def playlist_info(self, ref: PlaylistRef) -> PlaylistInfo:
        return await self._inner.playlist_info(ref)

    async def is_own_library(self, ref: PlaylistRef) -> bool:
        return await self._inner.is_own_library(ref)

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> PlaylistRef:
        return await self._inner.create_playlist(title, description, request_id=request_id)

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult:
        return await self._inner.add_tracks(playlist, tracks)

    def get_library(self) -> AsyncIterator[TrackCandidate]:
        return self._inner.get_library()

    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult:
        return await self._inner.add_to_library(tracks)

    def library_insert_order(self) -> InsertOrder:
        return self._inner.library_insert_order()

    def playlist_capacity(self) -> int | None:
        return self._inner.playlist_capacity()

    # --- внутреннее ---------------------------------------------------------------------

    def _key(self, kind: str, raw: str) -> str:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"search:{self.platform.value}:{kind}:{digest}"

    async def _read(self, key: str) -> list[TrackCandidate] | None:
        try:
            raw = await self._cache.get(key)
            if raw is None:
                return None
            return [_candidate_from_dict(item) for item in json.loads(raw)]
        except Exception as exc:  # кэш — оптимизация, его сбой не должен ронять поиск
            logger.warning("Кэш поиска недоступен при чтении (%s)", type(exc).__name__)
            return None

    async def _write(self, key: str, result: list[TrackCandidate]) -> None:
        ttl = self._ttl if result else min(self._ttl, _EMPTY_RESULT_MAX_TTL_SECONDS)
        try:
            payload = json.dumps([_candidate_to_dict(c) for c in result], ensure_ascii=False)
            await self._cache.set(key, payload, ttl)
        except Exception as exc:
            logger.warning("Кэш поиска недоступен при записи (%s)", type(exc).__name__)

import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, Final

from syncplaylists.integrations.platforms.soundcloud.api import SoundCloudApi
from syncplaylists.integrations.platforms.soundcloud.ids import (
    SoundCloudPlaylistId,
    parse_track_id,
)
from syncplaylists.integrations.platforms.soundcloud.mapping import (
    is_blocked,
    is_full_track,
    to_candidate,
)
from syncplaylists.integrations.platforms.soundcloud.transport import (
    SoundCloudBadRequestError,
    SoundCloudForbiddenError,
)
from syncplaylists.shared_kernel.domain.errors import (
    PlaylistNotFoundError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.search import (
    AddResult,
    InsertOrder,
    PlaylistInfo,
    PlaylistSnapshot,
    TrackCandidate,
    TrackQuery,
)
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)

logger = logging.getLogger(__name__)

_PLATFORM: Final = Platform.SOUNDCLOUD
_OWN_LIKES_ALIAS: Final = "you"


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class SoundCloudGateway:
    """MusicPlatformGateway для SoundCloud. Один инстанс — один аккаунт (id проверен
    через /me при подключении). Транспорт (v2 или официальный API) — в SoundCloudApi.

    Особенности SoundCloud:
    - поиска по ISRC нет, но ISRC из publisher_metadata отдаём в кандидатах;
    - в сете полными приходят только первые треки — остальные догружаются /tracks?ids=;
    - список треков сета заменяется целиком, ревизий нет; в сете не больше 500 треков;
    - лайк — по одному запросу на трек, новые лайки встают наверх (TOP);
    - дубли (то, что уже есть в назначении) не добавляются и считаются добавленными.
    """

    platform = _PLATFORM

    def __init__(
        self,
        api: SoundCloudApi,
        user_id: str,
        *,
        tracks_batch_size: int = 50,
        likes_page_size: int = 200,
        playlist_max_tracks: int = 500,
    ) -> None:
        self._api = api
        self._user_id = user_id
        self._tracks_batch_size = tracks_batch_size
        self._likes_page_size = likes_page_size
        self._playlist_max_tracks = playlist_max_tracks
        self._permalink: str | None = None

    # --- чтение -----------------------------------------------------------------------

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        text = f"{query.artist} {query.title}" if query.artist else query.title
        found = await self._api.search_tracks(text, limit)
        # BLOCK — недоступен в регионе: ни послушать, ни смысла добавлять.
        candidates = [to_candidate(t) for t in found if not is_blocked(t)]
        return [c for c in candidates if c is not None][:limit]

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]:
        return []  # поиска по ISRC в API нет — пусть работает FuzzySearchStrategy

    async def playlist_info(self, ref: PlaylistRef) -> PlaylistInfo:
        playlist_id = self._parse(ref)
        if playlist_id.likes_of is not None:
            user = await self._resolve_user(playlist_id.likes_of)
            return PlaylistInfo(
                ref=ref,
                title=f"Лайки {user.get('username') or playlist_id.likes_of}",
                description=None,
                owner_external_id=str(user["id"]),
                track_count=user.get("likes_count"),
            )
        data = await self._fetch_playlist(playlist_id)
        return PlaylistInfo(
            ref=ref,
            title=str(data.get("title") or ""),
            description=data.get("description") or None,
            owner_external_id=self._owner(data),
            track_count=data.get("track_count"),
        )

    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot:
        playlist_id = self._parse(ref)
        if playlist_id.likes_of is not None:
            user = await self._resolve_user(playlist_id.likes_of)
            tracks = [candidate async for candidate in self._liked_candidates(str(user["id"]))]
            return PlaylistSnapshot(
                ref=ref,
                title=f"Лайки {user.get('username') or playlist_id.likes_of}",
                description=None,
                tracks=tuple(tracks),
            )
        data = await self._fetch_playlist(playlist_id)
        tracks = await self._full_tracks(data)
        return PlaylistSnapshot(
            ref=ref,
            title=str(data.get("title") or ""),
            description=data.get("description") or None,
            tracks=tuple(tracks),
        )

    def get_library(self) -> AsyncIterator[TrackCandidate]:
        return self._liked_candidates(self._user_id)

    async def is_own_library(self, ref: PlaylistRef) -> bool:
        try:
            playlist_id = SoundCloudPlaylistId.parse(ref.external_id)
        except ValueError:
            return False
        if playlist_id.likes_of is None:
            return False
        if playlist_id.likes_of == _OWN_LIKES_ALIAS:
            return True
        return playlist_id.likes_of.lower() == (await self._own_permalink()).lower()

    def library_insert_order(self) -> InsertOrder:
        return InsertOrder.TOP

    def playlist_capacity(self) -> int | None:
        return self._playlist_max_tracks

    # --- запись -----------------------------------------------------------------------

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> PlaylistRef:
        data = await self._api.create_playlist(title, description)
        secret = data.get("secret_token") or None
        return PlaylistRef(_PLATFORM, SoundCloudPlaylistId.for_created(int(data["id"]), secret))

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult:
        playlist_id = self._parse(playlist)
        if playlist_id.likes_of is not None:
            raise PlaylistNotWritableError(_PLATFORM, "лайки — не плейлист")
        data = await self._fetch_playlist(playlist_id)
        if self._owner(data) != self._user_id:
            raise PlaylistNotWritableError(_PLATFORM, "плейлист другого аккаунта")

        present = [int(t["id"]) for t in data.get("tracks") or [] if isinstance(t, dict)]
        added: list[ExternalTrackRef] = []
        failed: list[ExternalTrackRef] = []
        new = list(self._new_tracks(tracks, set(present), added, failed))
        room = max(self._playlist_max_tracks - len(present), 0)
        if len(new) > room:
            logger.warning(
                "SoundCloud: в сет не влезло %s треков (лимит %s)",
                len(new) - room,
                self._playlist_max_tracks,
            )
            failed.extend(ref for ref, _ in new[room:])
            new = new[:room]
        if not new:
            return AddResult(added=tuple(added), failed=tuple(failed))

        try:
            await self._api.set_playlist_tracks(
                int(data["id"]),
                playlist_id.secret_token or data.get("secret_token") or None,
                [*present, *(track_id for _, track_id in new)],
            )
        except SoundCloudForbiddenError as exc:
            raise PlaylistNotWritableError(_PLATFORM, str(exc)) from exc
        added.extend(ref for ref, _ in new)
        return AddResult(added=tuple(added), failed=tuple(failed))

    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult:
        liked = await self._api.liked_track_ids(self._user_id)
        added: list[ExternalTrackRef] = []
        failed: list[ExternalTrackRef] = []
        # Пачек у лайков нет: по одному, в порядке списка (WriteTransferUseCase уже
        # развернул его под InsertOrder.TOP — последний лайк окажется сверху).
        for ref, track_id in self._new_tracks(tracks, liked, added, failed):
            try:
                await self._api.like(self._user_id, track_id)
            except (SoundCloudBadRequestError, SoundCloudForbiddenError, PlaylistNotFoundError):
                logger.warning("SoundCloud не принял лайк трека %s", track_id)
                failed.append(ref)
            else:
                added.append(ref)
        return AddResult(added=tuple(added), failed=tuple(failed))

    # --- внутреннее -------------------------------------------------------------------

    @staticmethod
    def _parse(ref: PlaylistRef) -> SoundCloudPlaylistId:
        try:
            return SoundCloudPlaylistId.parse(ref.external_id)
        except ValueError as exc:
            raise PlaylistNotFoundError(_PLATFORM, str(exc)) from exc

    @staticmethod
    def _new_tracks(
        tracks: Sequence[ExternalTrackRef],
        present: set[int],
        added: list[ExternalTrackRef],
        failed: list[ExternalTrackRef],
    ) -> Iterator[tuple[ExternalTrackRef, int]]:
        """Отсеивает то, что уже есть в назначении (считается добавленным — повтор
        run_write после сбоя не задвоит треки), дубли внутри запроса и чужие id."""
        seen = set(present)
        for ref in tracks:
            if ref.platform is not _PLATFORM:
                failed.append(ref)
                continue
            try:
                track_id = parse_track_id(ref.external_id)
            except ValueError:
                failed.append(ref)
                continue
            if track_id in seen:
                added.append(ref)
            else:
                seen.add(track_id)
                yield ref, track_id

    async def _fetch_playlist(self, playlist_id: SoundCloudPlaylistId) -> dict[str, Any]:
        try:
            if playlist_id.path is not None:
                data = await self._api.resolve(playlist_id.url)
            else:
                assert playlist_id.playlist_id is not None
                data = await self._api.playlist(playlist_id.playlist_id, playlist_id.secret_token)
        except SoundCloudForbiddenError as exc:
            # Закрытый чужой сет — для читающего его всё равно что нет.
            raise PlaylistNotFoundError(_PLATFORM, str(exc)) from exc
        if data.get("kind") not in (None, "playlist") or data.get("id") is None:
            raise PlaylistNotFoundError(_PLATFORM, "ссылка ведёт не на плейлист")
        return data

    async def _full_tracks(self, playlist: dict[str, Any]) -> list[TrackCandidate]:
        """Треки сета в его порядке: полные — как есть, заглушки — догрузкой пачками."""
        items = [t for t in playlist.get("tracks") or [] if isinstance(t, dict) and "id" in t]
        missing = [int(t["id"]) for t in items if not is_full_track(t)]
        loaded: dict[int, dict[str, Any]] = {}
        secret = playlist.get("secret_token") or None
        for chunk in _chunks(missing, self._tracks_batch_size):
            for track in await self._api.tracks(chunk, int(playlist["id"]), secret):
                loaded[int(track["id"])] = track

        candidates: list[TrackCandidate] = []
        skipped = 0
        for item in items:
            data = item if is_full_track(item) else loaded.get(int(item["id"]))
            candidate = to_candidate(data)
            if candidate is None:
                skipped += 1
            else:
                candidates.append(candidate)
        if skipped:
            logger.info("SoundCloud: пропущено недоступных треков: %s", skipped)
        return candidates

    async def _liked_candidates(self, user_id: str) -> AsyncIterator[TrackCandidate]:
        # Порядок — как у SoundCloud: сверху самый свежий лайк.
        async for track in self._api.liked_tracks(user_id, self._likes_page_size):
            candidate = to_candidate(track)
            if candidate is not None:
                yield candidate

    async def _resolve_user(self, permalink: str) -> dict[str, Any]:
        if permalink == _OWN_LIKES_ALIAS:
            return await self._api.me()
        try:
            data = await self._api.resolve(f"https://soundcloud.com/{permalink}")
        except SoundCloudForbiddenError as exc:
            raise PlaylistNotFoundError(_PLATFORM, str(exc)) from exc
        if data.get("kind") != "user" or data.get("id") is None:
            raise PlaylistNotFoundError(_PLATFORM, "пользователь не найден")
        return data

    async def _own_permalink(self) -> str:
        if self._permalink is None:
            self._permalink = str((await self._api.me()).get("permalink") or "")
        return self._permalink

    def _owner(self, playlist: dict[str, Any]) -> str | None:
        owner = playlist.get("user_id")
        if owner is None and isinstance(playlist.get("user"), dict):
            owner = playlist["user"].get("id")
        return str(owner) if owner is not None else None

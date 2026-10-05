import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from yandex_music.utils.difference import Difference

from syncplaylists.integrations.platforms.yandex.ids import (
    LIKES_PLAYLIST_KIND,
    YandexPlaylistId,
    YandexTrackId,
)
from syncplaylists.integrations.platforms.yandex.mapping import to_candidate
from syncplaylists.integrations.platforms.yandex.transport import (
    YandexBadRequestError,
    YandexForbiddenError,
    YandexWrongRevisionError,
)
from syncplaylists.shared_kernel.domain.errors import (
    PlatformUnavailableError,
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

_WRONG_REVISION_RETRIES: Final = 3


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass(slots=True)
class _WritablePlaylist:
    """То, что нужно для diff-вставки: ревизия и длина меняются после каждой пачки."""

    kind: int
    revision: int
    track_count: int
    track_ids: set[str] = field(default_factory=set)


class YandexGateway:
    """MusicPlatformGateway для Яндекс Музыки поверх yandex-music (ClientAsync) с
    httpx-транспортом (transport.py). Один инстанс — один аккаунт (uid проверен через
    профиль при подключении).

    Особенности Яндекса:
    - ISRC в API нет — search_by_isrc всегда пуст, матчинг идёт по тексту;
    - новые лайки встают наверх «Мне нравится» (library_insert_order = TOP);
    - плейлист меняется diff'ом к ревизии; устаревшая ревизия → перечитать и повторить;
    - дубли (то, что уже есть в назначении) не добавляются — по track_id без альбома.
    """

    platform = Platform.YANDEX

    def __init__(
        self,
        client: Any,
        uid: str,
        *,
        batch_size: int = 100,
        library_batch_preserves_order: bool = True,
    ) -> None:
        self._client = client
        self._uid = uid
        self._batch_size = batch_size
        # users_likes_tracks_add сохраняет порядок внутри пачки, первый трек — сверху
        # (live-тест test_batch_like_order_is_reported, ARCHITECTURE.md 11d). False —
        # запасной режим «по одному», если Яндекс это поведение поменяет.
        self._library_batch_preserves_order = library_batch_preserves_order
        self._login: str | None = None

    # --- чтение -----------------------------------------------------------------------

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        text = f"{query.artist} {query.title}" if query.artist else query.title
        result = await self._client.search(text, type_="track")
        tracks = result.tracks.results if result is not None and result.tracks else []
        candidates = [c for c in (to_candidate(t) for t in tracks) if c is not None]
        return candidates[:limit]

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]:
        return []  # поиска по ISRC у Яндекса нет — пусть работает FuzzySearchStrategy

    async def playlist_info(self, ref: PlaylistRef) -> PlaylistInfo:
        playlist = await self._fetch_playlist(ref)
        return PlaylistInfo(
            ref=ref,
            title=playlist.title or "",
            description=playlist.description or None,
            owner_external_id=self._owner_uid(playlist),
            track_count=playlist.track_count,
        )

    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot:
        playlist = await self._fetch_playlist(ref)
        tracks = await self._resolve_shorts(playlist.tracks or [])
        return PlaylistSnapshot(
            ref=ref,
            title=playlist.title or "",
            description=playlist.description or None,
            tracks=tuple(tracks),
        )

    async def get_library(self) -> AsyncIterator[TrackCandidate]:
        likes = await self._client.users_likes_tracks(user_id=self._uid)
        shorts = likes.tracks if likes is not None else []
        # Порядок — как у Яндекса: сверху самый свежий лайк.
        for chunk in _chunks(shorts, self._batch_size):
            for candidate in await self._resolve_shorts(chunk):
                yield candidate

    async def is_own_library(self, ref: PlaylistRef) -> bool:
        try:
            playlist_id = YandexPlaylistId.parse(ref.external_id)
        except ValueError:
            return False
        if playlist_id.kind != LIKES_PLAYLIST_KIND or playlist_id.owner is None:
            return False
        if playlist_id.owner == self._uid:
            return True
        return playlist_id.owner.lower() == (await self._own_login()).lower()

    def library_insert_order(self) -> InsertOrder:
        return InsertOrder.TOP

    def playlist_capacity(self) -> int | None:
        return None  # лимит у Яндекса есть (~10 000), но до него переносы не доходят

    # --- запись -----------------------------------------------------------------------

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> PlaylistRef:
        playlist = await self._client.users_playlists_create(
            title, visibility="private", user_id=self._uid
        )
        if description:
            await self._client.users_playlists_description(
                playlist.kind, description, user_id=self._uid
            )
        return PlaylistRef(Platform.YANDEX, f"{self._uid}:{playlist.kind}")

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult:
        target = await self._writable(playlist)
        added: list[ExternalTrackRef] = []
        failed: list[ExternalTrackRef] = []
        with_album: list[tuple[ExternalTrackRef, YandexTrackId]] = []
        without_album: list[tuple[ExternalTrackRef, YandexTrackId]] = []
        for ref, track_id in self._new_tracks(tracks, target.track_ids, added, failed):
            (with_album if track_id.album_id else without_album).append((ref, track_id))

        for chunk in _chunks(with_album, self._batch_size):
            await self._insert(playlist, target, [track_id for _, track_id in chunk])
            added.extend(ref for ref, _ in chunk)
        # Трек без альбома площадка может не принять в diff — пробуем по одному, чтобы
        # такой трек ушёл в failed, а не уронил пачку.
        for ref, track_id in without_album:
            try:
                await self._insert(playlist, target, [track_id])
            except YandexBadRequestError:
                logger.warning("Яндекс не принял трек без альбома %s в плейлист", track_id)
                failed.append(ref)
            else:
                added.append(ref)
        return AddResult(added=tuple(added), failed=tuple(failed))

    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult:
        likes = await self._client.users_likes_tracks(user_id=self._uid)
        liked = {str(short.id) for short in (likes.tracks if likes is not None else [])}
        added: list[ExternalTrackRef] = []
        failed: list[ExternalTrackRef] = []
        pending = list(self._new_tracks(tracks, liked, added, failed))

        # Цель: последний трек списка — на самом верху «Мне нравится»
        # (WriteTransferUseCase уже развернул список под InsertOrder.TOP). Каждая следующая
        # пачка ложится над предыдущей, а внутри пачки Яндекс ставит сверху ПЕРВЫЙ трек
        # (проверено live-тестом) — поэтому саму пачку отправляем развёрнутой.
        size = self._batch_size if self._library_batch_preserves_order else 1
        for chunk in _chunks(pending, size):
            ids = [str(track_id) for _, track_id in reversed(chunk)]
            try:
                ok = await self._client.users_likes_tracks_add(
                    ids if len(ids) > 1 else ids[0], user_id=self._uid
                )
            except YandexBadRequestError:
                ok = False
            (added if ok else failed).extend(ref for ref, _ in chunk)
        return AddResult(added=tuple(added), failed=tuple(failed))

    # --- внутреннее -------------------------------------------------------------------

    def _new_tracks(
        self,
        tracks: Sequence[ExternalTrackRef],
        present: set[str],
        added: list[ExternalTrackRef],
        failed: list[ExternalTrackRef],
    ) -> Iterator[tuple[ExternalTrackRef, YandexTrackId]]:
        """Отсеивает то, что уже есть в назначении (считается добавленным — повтор
        run_write после сбоя не задвоит треки), дубли внутри запроса и чужие id."""
        seen = set(present)
        for ref in tracks:
            try:
                track_id = YandexTrackId.parse(ref.external_id)
            except ValueError:
                failed.append(ref)
                continue
            if ref.platform is not Platform.YANDEX:
                failed.append(ref)
            elif track_id.track_id in seen:
                added.append(ref)
            else:
                seen.add(track_id.track_id)
                yield ref, track_id

    async def _fetch_playlist(self, ref: PlaylistRef) -> Any:
        try:
            playlist_id = YandexPlaylistId.parse(ref.external_id)
        except ValueError as exc:
            raise PlaylistNotFoundError(Platform.YANDEX, str(exc)) from exc
        try:
            if playlist_id.uuid is not None:
                playlist = await self._client.playlist(playlist_id.uuid)
            else:
                playlist = await self._client.users_playlists(
                    playlist_id.kind, user_id=playlist_id.owner
                )
        except YandexForbiddenError as exc:
            # Закрытый чужой плейлист — для читающего его всё равно что нет.
            raise PlaylistNotFoundError(Platform.YANDEX, str(exc)) from exc
        if playlist is None:
            raise PlaylistNotFoundError(Platform.YANDEX, ref.external_id)
        return playlist

    async def _writable(self, ref: PlaylistRef) -> _WritablePlaylist:
        playlist = await self._fetch_playlist(ref)
        if self._owner_uid(playlist) != self._uid:
            raise PlaylistNotWritableError(Platform.YANDEX, "плейлист другого аккаунта")
        return _WritablePlaylist(
            kind=playlist.kind,
            revision=playlist.revision or 0,
            track_count=playlist.track_count or len(playlist.tracks or []),
            track_ids={str(short.id) for short in (playlist.tracks or [])},
        )

    async def _insert(
        self, ref: PlaylistRef, target: _WritablePlaylist, track_ids: Sequence[YandexTrackId]
    ) -> None:
        for _ in range(_WRONG_REVISION_RETRIES):
            diff = Difference().add_insert(
                target.track_count,
                [{"id": t.track_id, "album_id": t.album_id} for t in track_ids],
            )
            try:
                updated = await self._client.users_playlists_change(
                    target.kind, diff.to_json(), target.revision, user_id=self._uid
                )
            except YandexWrongRevisionError:
                # Плейлист поменяли параллельно (сам пользователь в приложении) —
                # перечитываем ревизию/длину и вставляем в новый конец.
                fresh = await self._writable(ref)
                target.revision, target.track_count = fresh.revision, fresh.track_count
                continue
            except YandexForbiddenError as exc:
                raise PlaylistNotWritableError(Platform.YANDEX, str(exc)) from exc
            target.revision = updated.revision if updated is not None else target.revision + 1
            target.track_count += len(track_ids)
            target.track_ids.update(t.track_id for t in track_ids)
            return
        raise PlatformUnavailableError(Platform.YANDEX, "ревизия плейлиста меняется непрерывно")

    async def _resolve_shorts(self, shorts: Sequence[Any]) -> list[TrackCandidate]:
        """TrackShort (id + иногда полный трек) → кандидаты в том же порядке. Чего нет
        целиком — догружаем пачками через /tracks."""
        missing = [str(s.track_id) for s in shorts if s.track is None]
        loaded: dict[str, Any] = {}
        for chunk in _chunks(missing, self._batch_size):
            for track in await self._client.tracks(list(chunk)):
                loaded[str(track.id)] = track

        candidates: list[TrackCandidate] = []
        skipped = 0
        for short in shorts:
            track = short.track if short.track is not None else loaded.get(str(short.id))
            candidate = to_candidate(track)
            if candidate is None:
                skipped += 1
            else:
                candidates.append(candidate)
        if skipped:
            logger.info("Яндекс: пропущено недоступных треков: %s", skipped)
        return candidates

    async def _own_login(self) -> str:
        if self._login is None:
            status = await self._client.account_status()
            account = status.account if status is not None else None
            self._login = (account.login if account is not None else None) or ""
        return self._login

    @staticmethod
    def _owner_uid(playlist: Any) -> str | None:
        if playlist.owner is not None and playlist.owner.uid is not None:
            return str(playlist.owner.uid)
        return str(playlist.uid) if playlist.uid is not None else None

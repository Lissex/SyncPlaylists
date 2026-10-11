"""VK Музыка через браузерное расширение (транспорт EXTENSION, этап 4c-5).

Серверного пути у VK нет. Всё — из вкладки vk.ru пользователя: функция страницы берёт
веб-токен страницы из её localStorage, вызывает web.api.vk.ru и возвращает проекцию
ответа. Токен страницу не покидает (ARCHITECTURE.md, 11h). Капчу видит и проходит
человек в своём браузере.

`VkApi` — то, что нужно шлюзу (gateway.py); `ExtensionVkApi` — каждая операция задачей
расширению через ExtensionChannel, ответ — по строгой схеме extension_wire.py."""

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Final, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from syncplaylists.integrations.platforms.vk.extension_wire import (
    MAX_PAGE,
    MAX_SEARCH,
    MAX_TRACKS,
    VkAccount,
    VkAdded,
    VkCreated,
    VkDone,
    VkLibraryPage,
    VkPlaylist,
    VkPlaylistInfo,
    VkPlaylistPage,
    VkSearch,
    VkTrack,
    VkTracks,
)
from syncplaylists.integrations.platforms.vk.ids import VkPlaylistId, VkTrackId
from syncplaylists.modules.extension.application.operations import ExtensionOperationError
from syncplaylists.modules.extension.application.ports import ExtensionCall, ExtensionChannel
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import PlatformError, PlatformUnavailableError
from syncplaylists.shared_kernel.domain.value_objects import Platform

_Model = TypeVar("_Model", bound=BaseModel)
_Page = TypeVar("_Page", VkPlaylistPage, VkLibraryPage)

_PLATFORM: Final = Platform.VK
# Страниц больше этого — расширение зациклилось на offset; лучше сбой, чем вечное чтение.
_MAX_PAGES: Final = 200
# Список поменялся между страницами (добавили/удалили трек) — читаем заново.
_RESTARTS: Final = 3


class VkBadRequestError(PlatformError):
    """VK не принял запрос (error 100 и т.п.): повтор того же не поможет."""


class _ListChangedError(Exception):
    pass


class VkApi(Protocol):
    async def whoami(self) -> VkAccount: ...

    async def search(self, text: str, count: int) -> list[VkTrack]: ...

    async def tracks(self, ids: Sequence[VkTrackId]) -> list[VkTrack]: ...

    async def playlist_info(self, playlist: VkPlaylistId) -> VkPlaylist: ...

    async def playlist(self, playlist: VkPlaylistId) -> tuple[VkPlaylist, list[VkTrack]]: ...

    def library(self) -> AsyncIterator[VkTrack]: ...

    # Вся медиатека одним списком (для отсева уже добавленного перед записью).
    async def library_snapshot(self) -> list[VkTrack]: ...

    async def create_playlist(
        self, title: str, description: str, *, idempotency_key: str | None
    ) -> VkCreated: ...

    # Замена списка плейлиста целиком: audio_ids — "<owner>_<id>" в нужном порядке.
    async def set_playlist_tracks(
        self, playlist_id: int, audio_ids: Sequence[str], *, idempotency_key: str | None
    ) -> None: ...

    async def add_to_library(self, track: VkTrackId, *, idempotency_key: str | None) -> VkAdded: ...


class ExtensionVkApi:
    def __init__(
        self, channel: ExtensionChannel, access: AccountAccess, *, page_size: int = MAX_PAGE
    ) -> None:
        self._channel = channel
        self._access = access
        self._page_size = min(page_size, MAX_PAGE)

    async def _call(
        self,
        operation: str,
        model: type[_Model],
        args: Mapping[str, Any] | None = None,
        *,
        items: int = 0,
        idempotency_key: str | None = None,
    ) -> _Model:
        try:
            data = await self._channel.call(
                ExtensionCall(
                    user_id=self._access.user_id,
                    platform=_PLATFORM,
                    external_user_id=self._access.external_user_id,
                    operation=operation,
                    args=dict(args or {}),
                    items=items,
                    idempotency_key=idempotency_key,
                )
            )
        except ExtensionOperationError as exc:
            if exc.code == "bad_request":
                raise VkBadRequestError(_PLATFORM, str(exc)) from exc
            raise
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            # Тело ответа в лог не пишем: там данные пользователя.
            raise PlatformUnavailableError(
                _PLATFORM, f"расширение: ответ {operation} не по схеме"
            ) from exc

    # --- чтение -----------------------------------------------------------------------

    async def whoami(self) -> VkAccount:
        return await self._call("whoami", VkAccount)

    async def search(self, text: str, count: int) -> list[VkTrack]:
        args = {"q": text, "count": max(1, min(count, MAX_SEARCH))}
        return (await self._call("search", VkSearch, args)).tracks

    async def tracks(self, ids: Sequence[VkTrackId]) -> list[VkTrack]:
        loaded: list[VkTrack] = []
        for start in range(0, len(ids), MAX_TRACKS):
            chunk = [str(track_id) for track_id in ids[start : start + MAX_TRACKS]]
            loaded.extend((await self._call("tracks", VkTracks, {"ids": chunk})).tracks)
        return loaded

    async def playlist_info(self, playlist: VkPlaylistId) -> VkPlaylist:
        return (
            await self._call("playlist_info", VkPlaylistInfo, _playlist_args(playlist))
        ).playlist

    async def playlist(self, playlist: VkPlaylistId) -> tuple[VkPlaylist, list[VkTrack]]:
        for _ in range(_RESTARTS):
            header: VkPlaylist | None = None
            tracks: list[VkTrack] = []
            try:
                async for page in self._pages(
                    "playlist_page", VkPlaylistPage, _playlist_args(playlist)
                ):
                    header = header or page.playlist
                    tracks.extend(page.tracks)
            except _ListChangedError:
                continue
            assert header is not None
            return header, tracks
        raise PlatformUnavailableError(_PLATFORM, "плейлист меняется во время чтения")

    async def library(self) -> AsyncIterator[VkTrack]:
        # Медиатеку отдаём по мере чтения; если она поменялась посреди — сбой чтения
        # (повтор задачи прочитает заново), а не молча пропущенные треки.
        try:
            async for page in self._pages("library_page", VkLibraryPage, {}):
                for track in page.tracks:
                    yield track
        except _ListChangedError as exc:
            raise PlatformUnavailableError(_PLATFORM, "медиатека меняется во время чтения") from exc

    async def library_snapshot(self) -> list[VkTrack]:
        for _ in range(_RESTARTS):
            try:
                return [
                    track
                    async for page in self._pages("library_page", VkLibraryPage, {})
                    for track in page.tracks
                ]
            except _ListChangedError:
                continue
        raise PlatformUnavailableError(_PLATFORM, "медиатека меняется во время чтения")

    async def _pages(
        self, operation: str, model: type[_Page], args: Mapping[str, Any]
    ) -> AsyncIterator[_Page]:
        offset = 0
        total: int | None = None
        for _ in range(_MAX_PAGES):
            page = await self._call(
                operation, model, {**args, "offset": offset, "limit": self._page_size}
            )
            if total is not None and page.total != total:
                raise _ListChangedError
            total = page.total
            yield page
            if page.next_offset is None:
                return
            if page.next_offset <= offset:
                raise PlatformUnavailableError(_PLATFORM, f"расширение: {operation} без сдвига")
            offset = page.next_offset
        raise PlatformUnavailableError(_PLATFORM, f"расширение: слишком много страниц {operation}")

    # --- запись -----------------------------------------------------------------------

    async def create_playlist(
        self, title: str, description: str, *, idempotency_key: str | None
    ) -> VkCreated:
        return await self._call(
            "create_playlist",
            VkCreated,
            {"title": title, "description": description},
            idempotency_key=idempotency_key,
        )

    async def set_playlist_tracks(
        self, playlist_id: int, audio_ids: Sequence[str], *, idempotency_key: str | None
    ) -> None:
        await self._call(
            "set_playlist_tracks",
            VkDone,
            {"playlist_id": playlist_id, "audio_ids": list(audio_ids)},
            items=len(audio_ids),
            idempotency_key=idempotency_key,
        )

    async def add_to_library(self, track: VkTrackId, *, idempotency_key: str | None) -> VkAdded:
        return await self._call(
            "add_to_library",
            VkAdded,
            {
                "owner_id": track.owner_id,
                "audio_id": track.audio_id,
                "access_key": track.access_key,
            },
            items=1,
            idempotency_key=idempotency_key,
        )


def _playlist_args(playlist: VkPlaylistId) -> dict[str, Any]:
    return {
        "owner_id": playlist.owner_id,
        "playlist_id": playlist.playlist_id,
        "access_key": playlist.access_key,
    }

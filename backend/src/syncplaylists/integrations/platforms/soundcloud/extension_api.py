"""SoundCloud через браузерное расширение (транспорт EXTENSION, этап 4c-3).

Гибрид без токена на сервере: публичное (поиск, resolve, публичные сеты и треки, чужие
лайки) сервер читает сам, анонимно через api-v2 + client_id. Личное (профиль, свои
лайки, приватные сеты) и запись выполняет расширение в сессии пользователя на
soundcloud.com — там антибот DataDome проходит сам браузер (ARCHITECTURE.md, 11e/11h).

Это реализация SoundCloudApi: SoundCloudGateway поверх неё тот же, что у токена. Поэтому
ответы расширения (проекции, extension_wire.py) отдаются шлюзу dict-ами формы api-v2, а
ошибки — теми же исключениями, что у HTTP-транспорта."""

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Final, TypeVar

from pydantic import BaseModel, ValidationError

from syncplaylists.integrations.platforms.soundcloud.api import SoundCloudApi
from syncplaylists.integrations.platforms.soundcloud.extension_wire import (
    ScCreated,
    ScDone,
    ScLikedIdsPage,
    ScLikedTracksPage,
    ScMe,
    ScPlaylist,
    ScTracks,
)
from syncplaylists.integrations.platforms.soundcloud.transport import SoundCloudForbiddenError
from syncplaylists.modules.extension.application.ports import ExtensionCall, ExtensionChannel
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import (
    PlatformUnavailableError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform

_Model = TypeVar("_Model", bound=BaseModel)

_PLATFORM: Final = Platform.SOUNDCLOUD
# Страниц больше этого — расширение зациклилось на курсоре; лучше сбой, чем вечное чтение.
_MAX_PAGES: Final = 1000


class ExtensionSoundCloudApi:
    def __init__(
        self, channel: ExtensionChannel, access: AccountAccess, public: SoundCloudApi
    ) -> None:
        self._channel = channel
        self._access = access
        self._public = public
        # Транспорт анонимных запросов сервера (атрибут протокола SoundCloudApi).
        self.transport = public.transport

    # --- в браузере -------------------------------------------------------------------

    async def _call(
        self,
        operation: str,
        model: type[_Model],
        args: Mapping[str, Any] | None = None,
        *,
        items: int = 0,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
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
        except PlaylistNotWritableError as exc:
            # Расширение сообщает «403 на запрос». Что он значит, решает шлюз по контексту
            # (лайк не принят, чужой/закрытый сет) — как у HTTP-транспорта.
            raise SoundCloudForbiddenError(_PLATFORM, str(exc)) from exc
        try:
            parsed = model.model_validate(data)
        except ValidationError as exc:
            # Тело ответа в лог не пишем: там данные пользователя.
            raise PlatformUnavailableError(
                _PLATFORM, f"расширение: ответ {operation} не по схеме"
            ) from exc
        return parsed.model_dump(exclude_none=True, by_alias=True)

    def _is_own(self, user_id: str) -> bool:
        return user_id == self._access.external_user_id

    async def me(self) -> dict[str, Any]:
        return await self._call("whoami", ScMe)

    async def liked_tracks(self, user_id: str, page_size: int) -> AsyncIterator[dict[str, Any]]:
        if not self._is_own(user_id):
            # Чужие лайки публичны — их читает сервер.
            async for track in self._public.liked_tracks(user_id, page_size):
                yield track
            return
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            page = await self._call(
                "liked_tracks_page", ScLikedTracksPage, {"cursor": cursor, "limit": page_size}
            )
            for track in page["tracks"]:
                yield track
            cursor = page.get("next_cursor")
            if cursor is None:
                return
        raise PlatformUnavailableError(_PLATFORM, "расширение: слишком много страниц лайков")

    async def liked_track_ids(self, user_id: str) -> set[int]:
        self._ensure_own(user_id)
        ids: set[int] = set()
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            page = await self._call("liked_track_ids_page", ScLikedIdsPage, {"cursor": cursor})
            ids.update(page["ids"])
            cursor = page.get("next_cursor")
            if cursor is None:
                return ids
        raise PlatformUnavailableError(_PLATFORM, "расширение: слишком много страниц лайков")

    async def like(self, user_id: str, track_id: int) -> None:
        self._ensure_own(user_id)
        await self._call("like", ScDone, {"track_id": track_id}, items=1)

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> dict[str, Any]:
        return await self._call(
            "create_playlist",
            ScCreated,
            {"title": title, "description": description},
            idempotency_key=f"create_playlist:{request_id}" if request_id else None,
        )

    async def set_playlist_tracks(
        self, playlist_id: int, secret_token: str | None, track_ids: Sequence[int]
    ) -> None:
        # Замена списка целиком — идемпотентна сама: повтор после обрыва даёт тот же сет.
        await self._call(
            "set_playlist_tracks",
            ScDone,
            {
                "playlist_id": playlist_id,
                "secret": secret_token,
                "track_ids": list(track_ids),
            },
        )

    def _ensure_own(self, user_id: str) -> None:
        if not self._is_own(user_id):
            raise PlaylistNotWritableError(_PLATFORM, "операция с чужим аккаунтом")

    # --- приватное — в браузере, публичное — на сервере --------------------------------

    async def playlist(self, playlist_id: int, secret_token: str | None) -> dict[str, Any]:
        if secret_token:
            return await self._call(
                "playlist", ScPlaylist, {"playlist_id": playlist_id, "secret": secret_token}
            )
        return await self._public.playlist(playlist_id, None)

    async def tracks(
        self, ids: Sequence[int], playlist_id: int | None, secret_token: str | None
    ) -> list[dict[str, Any]]:
        if playlist_id is not None and secret_token:
            # Приватные треки приватного сета отдаются только владельцу/по секрету.
            result = await self._call(
                "tracks",
                ScTracks,
                {"ids": list(ids), "playlist_id": playlist_id, "secret": secret_token},
            )
            return list(result["tracks"])
        return await self._public.tracks(ids, None, None)

    # --- публичное — на сервере -------------------------------------------------------

    async def resolve(self, url: str) -> dict[str, Any]:
        return await self._public.resolve(url)

    async def search_tracks(self, text: str, limit: int) -> list[dict[str, Any]]:
        return await self._public.search_tracks(text, limit)

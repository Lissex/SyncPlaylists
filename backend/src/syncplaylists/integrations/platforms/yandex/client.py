"""Клиент Яндекс Музыки, с которым работает YandexGateway, — ровно те методы, что он
вызывает. Реализаций две: LibraryYandexClient (yandex-music ClientAsync по токену, сервер)
и ExtensionYandexClient (операции в браузере пользователя, этап 4c-4). Обе отдают модели
yandex-music (Track, Playlist, TracksList, …), поэтому маппинг и логика шлюза общие.

Модели библиотеки без аннотаций (py.typed нет) — для mypy это Any."""

from collections.abc import Sequence
from typing import Any, Protocol


class YandexMusicClient(Protocol):
    async def search(self, text: str, *, type_: str) -> Any: ...

    async def users_playlists(self, kind: int, *, user_id: str | None) -> Any: ...

    async def playlist(self, playlist_uuid: str) -> Any: ...

    async def users_likes_tracks(self, *, user_id: str) -> Any: ...

    async def tracks(self, track_ids: Sequence[str]) -> list[Any]: ...

    async def users_playlists_create(
        self, title: str, *, visibility: str, user_id: str, request_id: str | None = None
    ) -> Any: ...

    async def users_playlists_description(
        self, kind: int, description: str, *, user_id: str
    ) -> Any: ...

    async def users_playlists_change(
        self, kind: int, diff: str, revision: int, *, user_id: str
    ) -> Any: ...

    async def users_likes_tracks_add(self, track_ids: list[str] | str, *, user_id: str) -> bool: ...

    async def account_status(self) -> Any: ...


class LibraryYandexClient:
    """YandexMusicClient поверх ClientAsync библиотеки (транспорт — HttpxYandexRequest).
    Создание плейлиста по токену не идемпотентно — request_id здесь не нужен."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def search(self, text: str, *, type_: str) -> Any:
        return await self._client.search(text, type_=type_)

    async def users_playlists(self, kind: int, *, user_id: str | None) -> Any:
        return await self._client.users_playlists(kind, user_id=user_id)

    async def playlist(self, playlist_uuid: str) -> Any:
        return await self._client.playlist(playlist_uuid)

    async def users_likes_tracks(self, *, user_id: str) -> Any:
        return await self._client.users_likes_tracks(user_id=user_id)

    async def tracks(self, track_ids: Sequence[str]) -> list[Any]:
        return list(await self._client.tracks(list(track_ids)))

    async def users_playlists_create(
        self, title: str, *, visibility: str, user_id: str, request_id: str | None = None
    ) -> Any:
        return await self._client.users_playlists_create(
            title, visibility=visibility, user_id=user_id
        )

    async def users_playlists_description(
        self, kind: int, description: str, *, user_id: str
    ) -> Any:
        return await self._client.users_playlists_description(kind, description, user_id=user_id)

    async def users_playlists_change(
        self, kind: int, diff: str, revision: int, *, user_id: str
    ) -> Any:
        return await self._client.users_playlists_change(kind, diff, revision, user_id=user_id)

    async def users_likes_tracks_add(self, track_ids: list[str] | str, *, user_id: str) -> bool:
        return bool(await self._client.users_likes_tracks_add(track_ids, user_id=user_id))

    async def account_status(self) -> Any:
        return await self._client.account_status()

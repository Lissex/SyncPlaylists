"""Эндпоинты SoundCloud: внутренний api-v2 (UNOFFICIAL) и официальный API (OFFICIAL).

Логика шлюза (gateway.py) одна; отличаются только пути и формы тел. Пути v2 сверены с
таблицей эндпоинтов в JS сайта (2026-10-03): лайк — PUT users/:userId/track_likes/:id,
id лайков — GET me/track_likes/ids, сет — POST playlists / PUT playlists/:id.
Официальные — по документации developers.soundcloud.com (live не проверены: нет
приложения с Artist Pro).
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any, Final, Protocol

from syncplaylists.integrations.platforms.soundcloud.transport import SoundCloudTransport

_IDS_PAGE_SIZE: Final = 5000


def _collection(body: Any) -> list[Any]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("collection"), list):
        return list(body["collection"])
    return []


def _next_href(body: Any) -> str | None:
    href = body.get("next_href") if isinstance(body, dict) else None
    return href if isinstance(href, str) and href.startswith("https://") else None


class SoundCloudApi(Protocol):
    transport: SoundCloudTransport

    async def me(self) -> dict[str, Any]: ...

    async def resolve(self, url: str) -> dict[str, Any]: ...

    async def playlist(self, playlist_id: int, secret_token: str | None) -> dict[str, Any]: ...

    # Порядок ответа не гарантирован — вызывающий раскладывает по id сам.
    async def tracks(
        self, ids: Sequence[int], playlist_id: int | None, secret_token: str | None
    ) -> list[dict[str, Any]]: ...

    async def search_tracks(self, text: str, limit: int) -> list[dict[str, Any]]: ...

    # Треки из лайков пользователя, свежие первыми; заглушки удалённых не отдаёт.
    def liked_tracks(self, user_id: str, page_size: int) -> AsyncIterator[dict[str, Any]]: ...

    async def liked_track_ids(self, user_id: str) -> set[int]: ...

    async def like(self, user_id: str, track_id: int) -> None: ...

    # request_id — ключ идемпотентности создания (повтор с тем же ключом не создаёт второй
    # сет); серверные транспорты его не используют, расширение — да (11h).
    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> dict[str, Any]: ...

    # Список треков сета заменяется целиком (вставки по позиции у SoundCloud нет).
    async def set_playlist_tracks(
        self, playlist_id: int, secret_token: str | None, track_ids: Sequence[int]
    ) -> None: ...


class V2Api:
    def __init__(self, transport: SoundCloudTransport) -> None:
        self.transport = transport

    async def me(self) -> dict[str, Any]:
        return dict(await self.transport.get("/me"))

    async def resolve(self, url: str) -> dict[str, Any]:
        return dict(await self.transport.get("/resolve", {"url": url}))

    async def playlist(self, playlist_id: int, secret_token: str | None) -> dict[str, Any]:
        params = {"secret_token": secret_token} if secret_token else None
        return dict(await self.transport.get(f"/playlists/{playlist_id}", params))

    async def tracks(
        self, ids: Sequence[int], playlist_id: int | None, secret_token: str | None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"ids": ",".join(str(i) for i in ids)}
        # Приватные треки приватного сета отдаются только вместе с его секретом.
        if playlist_id is not None and secret_token:
            params["playlistId"] = playlist_id
            params["playlistSecretToken"] = secret_token
        return [t for t in _collection(await self.transport.get("/tracks", params)) if t]

    async def search_tracks(self, text: str, limit: int) -> list[dict[str, Any]]:
        body = await self.transport.get("/search/tracks", {"q": text, "limit": limit})
        return _collection(body)

    async def liked_tracks(self, user_id: str, page_size: int) -> AsyncIterator[dict[str, Any]]:
        path: str | None = f"/users/{user_id}/track_likes"
        params: dict[str, Any] | None = {"limit": page_size, "linked_partitioning": 1}
        while path is not None:
            body = await self.transport.get(path, params)
            for like in _collection(body):
                track = like.get("track") if isinstance(like, dict) else None
                if isinstance(track, dict):
                    yield track
            path, params = _next_href(body), None

    async def liked_track_ids(self, user_id: str) -> set[int]:
        ids: set[int] = set()
        path: str | None = "/me/track_likes/ids"
        params: dict[str, Any] | None = {"limit": _IDS_PAGE_SIZE, "linked_partitioning": 1}
        while path is not None:
            body = await self.transport.get(path, params)
            ids.update(int(i) for i in _collection(body) if isinstance(i, int))
            path, params = _next_href(body), None
        return ids

    async def like(self, user_id: str, track_id: int) -> None:
        await self.transport.request("PUT", f"/users/{user_id}/track_likes/{track_id}")

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> dict[str, Any]:
        playlist: dict[str, Any] = {"title": title, "sharing": "private", "tracks": []}
        if description:
            playlist["description"] = description
        return dict(await self.transport.request("POST", "/playlists", json={"playlist": playlist}))

    async def set_playlist_tracks(
        self, playlist_id: int, secret_token: str | None, track_ids: Sequence[int]
    ) -> None:
        params = {"secret_token": secret_token} if secret_token else None
        await self.transport.request(
            "PUT",
            f"/playlists/{playlist_id}",
            params=params,
            json={"playlist": {"tracks": list(track_ids)}},
        )


class OfficialApi:
    def __init__(self, transport: SoundCloudTransport) -> None:
        self.transport = transport

    async def me(self) -> dict[str, Any]:
        return dict(await self.transport.get("/me"))

    async def resolve(self, url: str) -> dict[str, Any]:
        return dict(await self.transport.get("/resolve", {"url": url}))

    async def playlist(self, playlist_id: int, secret_token: str | None) -> dict[str, Any]:
        params = {"secret_token": secret_token} if secret_token else None
        return dict(await self.transport.get(f"/playlists/{playlist_id}", params))

    async def tracks(
        self, ids: Sequence[int], playlist_id: int | None, secret_token: str | None
    ) -> list[dict[str, Any]]:
        body = await self.transport.get("/tracks", {"ids": ",".join(str(i) for i in ids)})
        return [t for t in _collection(body) if t]

    async def search_tracks(self, text: str, limit: int) -> list[dict[str, Any]]:
        body = await self.transport.get(
            "/tracks", {"q": text, "limit": limit, "linked_partitioning": "true"}
        )
        return _collection(body)

    async def liked_tracks(self, user_id: str, page_size: int) -> AsyncIterator[dict[str, Any]]:
        path: str | None = "/me/likes/tracks"
        params: dict[str, Any] | None = {"limit": page_size, "linked_partitioning": "true"}
        while path is not None:
            body = await self.transport.get(path, params)
            for track in _collection(body):
                if isinstance(track, dict):
                    yield track
            path, params = _next_href(body), None

    async def liked_track_ids(self, user_id: str) -> set[int]:
        return {
            int(track["id"])
            async for track in self.liked_tracks(user_id, 200)
            if isinstance(track.get("id"), int)
        }

    async def like(self, user_id: str, track_id: int) -> None:
        await self.transport.request("POST", f"/likes/tracks/{track_id}")

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> dict[str, Any]:
        playlist: dict[str, Any] = {"title": title, "sharing": "private", "tracks": []}
        if description:
            playlist["description"] = description
        return dict(await self.transport.request("POST", "/playlists", json={"playlist": playlist}))

    async def set_playlist_tracks(
        self, playlist_id: int, secret_token: str | None, track_ids: Sequence[int]
    ) -> None:
        await self.transport.request(
            "PUT",
            f"/playlists/{playlist_id}",
            json={"playlist": {"tracks": [{"id": track_id} for track_id in track_ids]}},
        )

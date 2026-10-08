"""Яндекс Музыка через браузерное расширение (транспорт EXTENSION, этап 4c-4).

Поиск, чтение и запись идут из вкладки music.yandex.ru пользователя: fetch к
api.music.yandex.ru с cookie сессии (credentials: "include"). Токена Яндекса нет ни на
сервере, ни в расширении (ARCHITECTURE.md, 11h). Капчу антибота видит и проходит человек
в своём браузере, серверный общий bucket (11g) к этим запросам не относится — у клиента
нет PlatformRateLimiter.

Это реализация YandexMusicClient: YandexGateway поверх неё тот же, что у токена. Ответы
расширения (проекции, extension_wire.py) превращаются в модели yandex-music через
`de_json`, ошибки — в те же исключения, что у HTTP-транспорта (transport.py)."""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final, TypeVar

from pydantic import BaseModel, ValidationError
from yandex_music import Playlist, Search, Status, Track, TracksList

from syncplaylists.integrations.platforms.yandex.extension_wire import (
    MAX_PAGE,
    MAX_TRACKS,
    YmAccount,
    YmChanged,
    YmCreated,
    YmDone,
    YmLibraryPage,
    YmPlaylistPage,
    YmSearch,
    YmTracks,
)
from syncplaylists.integrations.platforms.yandex.transport import (
    YandexBadRequestError,
    YandexForbiddenError,
    YandexWrongRevisionError,
)
from syncplaylists.modules.extension.application.operations import ExtensionOperationError
from syncplaylists.modules.extension.application.ports import ExtensionCall, ExtensionChannel
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import (
    PlatformUnavailableError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform

_Model = TypeVar("_Model", bound=BaseModel)

_PLATFORM: Final = Platform.YANDEX
# Страниц больше этого — расширение зациклилось на offset; лучше сбой, чем вечное чтение.
_MAX_PAGES: Final = 100
# Список поменялся между страницами (новый лайк, правка плейлиста) — читаем заново.
_RESTARTS: Final = 3


class ExtensionYandexClient:
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
                    idempotency_key=idempotency_key,
                )
            )
        except PlaylistNotWritableError as exc:
            # «403 на запрос»: что он значит (чужой плейлист, закрытый на чтение), решает
            # шлюз по контексту — как с 403 HTTP-транспорта.
            raise YandexForbiddenError(_PLATFORM, str(exc)) from exc
        except ExtensionOperationError as exc:
            if exc.code == "conflict":
                raise YandexWrongRevisionError(_PLATFORM, str(exc)) from exc
            if exc.code == "bad_request":
                raise YandexBadRequestError(_PLATFORM, str(exc)) from exc
            raise
        try:
            parsed = model.model_validate(data)
        except ValidationError as exc:
            # Тело ответа в лог не пишем: там данные пользователя.
            raise PlatformUnavailableError(
                _PLATFORM, f"расширение: ответ {operation} не по схеме"
            ) from exc
        return parsed.model_dump(exclude_none=True, by_alias=True)

    def _ensure_own(self, user_id: str | None) -> str:
        own = self._access.external_user_id
        if user_id is not None and user_id != own:
            raise PlaylistNotWritableError(_PLATFORM, "операция с чужим аккаунтом")
        return own

    async def _pages(
        self, operation: str, model: type[_Model], args: Mapping[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Первая страница (заголовок) и все записи. Страницы — срезы одной ревизии:
        если список поменялся между ними, расширение отвечает conflict и чтение
        начинается сначала."""
        for _ in range(_RESTARTS):
            try:
                return await self._read_pages(operation, model, args)
            except YandexWrongRevisionError:
                continue
        raise PlatformUnavailableError(_PLATFORM, "список меняется во время чтения")

    async def _read_pages(
        self, operation: str, model: type[_Model], args: Mapping[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        first: dict[str, Any] | None = None
        items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            page = await self._call(
                operation,
                model,
                {
                    **args,
                    "offset": offset,
                    "limit": self._page_size,
                    "revision": None if first is None else first["revision"],
                },
            )
            if first is None:
                first = page
            items.extend(page["tracks"])
            following = page.get("nextOffset")
            if following is None:
                return first, items
            if following <= offset:
                raise PlatformUnavailableError(_PLATFORM, f"расширение: {operation} без сдвига")
            offset = following
        raise PlatformUnavailableError(_PLATFORM, f"расширение: слишком много страниц {operation}")

    # --- чтение -----------------------------------------------------------------------

    async def search(self, text: str, *, type_: str) -> Any:
        if type_ != "track":
            raise ValueError("через расширение ищем только треки")
        found = (await self._call("search", YmSearch, {"text": text}))["tracks"]
        return Search.de_json(
            {
                "searchRequestId": "",
                "text": text,
                "type": "track",
                "page": 0,
                "perPage": len(found),
                "misspellCorrected": False,
                "nocorrect": False,
                "tracks": {
                    "type": "track",
                    "total": len(found),
                    "perPage": len(found),
                    "order": 0,
                    "results": found,
                },
            },
            None,
        )

    async def users_playlists(self, kind: int, *, user_id: str | None) -> Any:
        owner = user_id or self._access.external_user_id
        return await self._playlist({"owner": owner, "kind": kind, "uuid": None})

    async def playlist(self, playlist_uuid: str) -> Any:
        return await self._playlist({"owner": None, "kind": None, "uuid": playlist_uuid})

    async def _playlist(self, args: Mapping[str, Any]) -> Any:
        header, shorts = await self._pages("playlist_page", YmPlaylistPage, args)
        header.pop("nextOffset", None)
        if "owner" in header:
            header["owner"].setdefault("login", "")  # конструктор User требует login
        return Playlist.de_json({**header, "tracks": shorts}, None)

    async def users_likes_tracks(self, *, user_id: str) -> Any:
        own = self._ensure_own(user_id)
        header, shorts = await self._pages("library_ids_page", YmLibraryPage, {})
        library = {"uid": own, "revision": header["revision"], "tracks": shorts}
        return TracksList.de_json(library, None)

    async def tracks(self, track_ids: Sequence[str]) -> list[Any]:
        loaded: list[Any] = []
        for start in range(0, len(track_ids), MAX_TRACKS):
            chunk = [str(track_id) for track_id in track_ids[start : start + MAX_TRACKS]]
            data = await self._call("tracks", YmTracks, {"ids": chunk})
            loaded.extend(Track.de_list(data["tracks"], None))
        return loaded

    async def account_status(self) -> Any:
        profile = await self._call("whoami", YmAccount)
        account = {
            # now/serviceAvailable требует конструктор Account; шлюзу они не нужны.
            "now": "",
            "serviceAvailable": True,
            "uid": profile["id"],
            "login": profile.get("login"),
            "displayName": profile.get("username"),
        }
        return Status.de_json({"account": account}, None)

    # --- запись -----------------------------------------------------------------------

    async def users_playlists_create(
        self, title: str, *, visibility: str, user_id: str, request_id: str | None = None
    ) -> Any:
        own = self._ensure_own(user_id)
        if visibility != "private":
            raise ValueError("через расширение создаём только приватные плейлисты")
        created = await self._call(
            "create_playlist",
            YmCreated,
            {"title": title},
            idempotency_key=f"create_playlist:{request_id}" if request_id else None,
        )
        return Playlist.de_json({**created, "uid": own}, None)

    async def users_playlists_description(
        self, kind: int, description: str, *, user_id: str
    ) -> Any:
        self._ensure_own(user_id)
        # Повтор с тем же текстом даёт то же описание — ключ идемпотентности не нужен.
        await self._call("set_description", YmDone, {"kind": kind, "description": description})
        return None

    async def users_playlists_change(
        self, kind: int, diff: str, revision: int, *, user_id: str
    ) -> Any:
        """Шлюз присылает diff yandex-music (JSON-строка). В браузер уходит не она, а
        разобранная вставка: расширение принимает только insert в конец списка, сырой
        diff оно не исполняет. Повтор безопасен: устаревшая ревизия → conflict, шлюз
        перечитывает плейлист и отсеивает уже добавленное."""
        self._ensure_own(user_id)
        at, tracks = _single_insert(diff)
        changed = await self._call(
            "add_tracks",
            YmChanged,
            {"kind": kind, "revision": revision, "at": at, "tracks": tracks},
        )
        return Playlist.de_json({"kind": kind, **changed}, None)

    async def users_likes_tracks_add(self, track_ids: list[str] | str, *, user_id: str) -> bool:
        self._ensure_own(user_id)
        ids = [track_ids] if isinstance(track_ids, str) else list(track_ids)
        # Лайк уже лайкнутого ничего не меняет — повтор безопасен без ключа.
        await self._call("like_many", YmDone, {"ids": ids})
        return True


def _single_insert(diff: str) -> tuple[int, list[dict[str, str | None]]]:
    try:
        operations = json.loads(diff)
        (operation,) = operations
        if operation["op"] != "insert":
            raise ValueError(operation["op"])
        at = int(operation["at"])
        tracks = [
            {"id": str(track["id"]), "album_id": _optional_str(track.get("albumId"))}
            for track in operation["tracks"]
        ]
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("через расширение поддерживается только одна вставка") from exc
    return at, tracks


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)

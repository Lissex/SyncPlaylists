from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeVar

from pydantic import BaseModel, ValidationError

from syncplaylists.integrations.platforms.extension.wire import (
    AddResultWire,
    CreatedPlaylistResult,
    OwnLibraryResult,
    PageResult,
    PlaylistInfoResult,
    TracksResult,
)
from syncplaylists.modules.extension.application.ports import ExtensionCall, ExtensionChannel
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import PlatformUnavailableError
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

_Model = TypeVar("_Model", bound=BaseModel)

# Страниц больше этого — расширение зациклилось на курсоре; лучше сбой, чем вечное чтение.
_MAX_PAGES: Final = 1000


@dataclass(frozen=True, slots=True)
class ExtensionPlatformTraits:
    """То, что про площадку известно заранее и в браузер за этим не ходят."""

    insert_order: InsertOrder = InsertOrder.TOP
    playlist_capacity: int | None = None


_DEFAULT_TRAITS: Final = ExtensionPlatformTraits()


class ExtensionGateway:
    """MusicPlatformGateway, все операции которого выполняет браузерное расширение
    пользователя (транспорт EXTENSION): операции из фиксированного реестра, ответы
    проверяются схемами (wire.py). Длинные чтения — постранично, по задаче на страницу.

    Общий для площадок, у которых вся логика — в расширении. Площадки, где публичное
    можно читать с сервера (SoundCloud v2, 4c-3), получат свой гибридный шлюз."""

    def __init__(
        self,
        channel: ExtensionChannel,
        access: AccountAccess,
        traits: ExtensionPlatformTraits = _DEFAULT_TRAITS,
    ) -> None:
        self.platform = access.platform
        self._channel = channel
        self._access = access
        self._traits = traits

    async def _call(
        self,
        operation: str,
        model: type[_Model],
        args: Mapping[str, Any] | None = None,
        *,
        items: int = 0,
        idempotency_key: str | None = None,
    ) -> _Model:
        data = await self._channel.call(
            ExtensionCall(
                user_id=self._access.user_id,
                platform=self.platform,
                external_user_id=self._access.external_user_id,
                operation=operation,
                args=dict(args or {}),
                items=items,
                idempotency_key=idempotency_key,
            )
        )
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            # Расширение ответило не по контракту операции — как сбой площадки (повтор);
            # тело ответа в лог не пишем.
            raise PlatformUnavailableError(
                self.platform, f"расширение: ответ {operation} не по схеме"
            ) from exc

    def _ref(self, track_id: str) -> ExternalTrackRef:
        return ExternalTrackRef(self.platform, track_id)

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        args: dict[str, Any] = {"title": query.title, "artist": query.artist, "limit": limit}
        if query.isrc is not None:
            args["isrc"] = query.isrc.value
        if query.duration is not None:
            args["duration_ms"] = query.duration.milliseconds
        result = await self._call("search", TracksResult, args)
        return [t.to_domain(self.platform) for t in result.tracks[:limit]]

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]:
        result = await self._call("search_by_isrc", TracksResult, {"isrc": isrc.value})
        return [t.to_domain(self.platform) for t in result.tracks]

    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot:
        tracks: list[TrackCandidate] = []
        title, description = "", None
        cursor: str | None = None
        for page_number in range(_MAX_PAGES):
            page = await self._call(
                "playlist_page", PageResult, {"playlist_id": ref.external_id, "cursor": cursor}
            )
            if page_number == 0:
                title, description = page.title or "", page.description
            tracks.extend(t.to_domain(self.platform) for t in page.tracks)
            if page.next_cursor is None:
                return PlaylistSnapshot(
                    ref=ref, title=title, description=description, tracks=tuple(tracks)
                )
            cursor = page.next_cursor
        raise PlatformUnavailableError(self.platform, "расширение: слишком много страниц")

    async def playlist_info(self, ref: PlaylistRef) -> PlaylistInfo:
        info = await self._call(
            "playlist_info", PlaylistInfoResult, {"playlist_id": ref.external_id}
        )
        return PlaylistInfo(
            ref=ref,
            title=info.title,
            description=info.description,
            owner_external_id=info.owner_external_id,
            track_count=info.track_count,
        )

    async def is_own_library(self, ref: PlaylistRef) -> bool:
        result = await self._call(
            "is_own_library", OwnLibraryResult, {"playlist_id": ref.external_id}
        )
        return result.own

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> PlaylistRef:
        result = await self._call(
            "create_playlist",
            CreatedPlaylistResult,
            {"title": title, "description": description},
            idempotency_key=f"create_playlist:{request_id}" if request_id else None,
        )
        return PlaylistRef(self.platform, result.playlist_id)

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult:
        result = await self._call(
            "add_tracks",
            AddResultWire,
            {"playlist_id": playlist.external_id, "track_ids": [t.external_id for t in tracks]},
            items=len(tracks),
        )
        return self._add_result(tracks, result)

    async def get_library(self) -> AsyncIterator[TrackCandidate]:
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            page = await self._call("library_page", PageResult, {"cursor": cursor})
            for track in page.tracks:
                yield track.to_domain(self.platform)
            if page.next_cursor is None:
                return
            cursor = page.next_cursor
        raise PlatformUnavailableError(self.platform, "расширение: слишком много страниц")

    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult:
        result = await self._call(
            "add_to_library",
            AddResultWire,
            {"track_ids": [t.external_id for t in tracks]},
            items=len(tracks),
        )
        return self._add_result(tracks, result)

    def library_insert_order(self) -> InsertOrder:
        return self._traits.insert_order

    def playlist_capacity(self) -> int | None:
        return self._traits.playlist_capacity

    def _add_result(self, requested: Sequence[ExternalTrackRef], wire: AddResultWire) -> AddResult:
        # Доверяем только трекам из запроса: чужие id в ответе расширения игнорируются.
        asked = {t.external_id for t in requested}
        failed = {i for i in wire.failed if i in asked}
        added = [t for t in requested if t.external_id not in failed]
        return AddResult(added=tuple(added), failed=tuple(self._ref(i) for i in failed))


class ExtensionGatewayBuilder:
    """GatewayBuilder транспорта EXTENSION для площадок с общим шлюзом."""

    def __init__(
        self, channel: ExtensionChannel, traits: Mapping[Platform, ExtensionPlatformTraits]
    ) -> None:
        self._channel = channel
        self._traits = dict(traits)

    def __call__(self, access: AccountAccess) -> ExtensionGateway:
        return ExtensionGateway(
            self._channel, access, self._traits.get(access.platform, _DEFAULT_TRAITS)
        )

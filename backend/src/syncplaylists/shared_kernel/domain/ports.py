from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from syncplaylists.shared_kernel.domain.search import (
    AddResult,
    InsertOrder,
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


class MusicPlatformGateway(Protocol):
    platform: Platform

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]: ...

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]: ...

    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot: ...

    async def create_playlist(self, title: str, description: str | None) -> PlaylistRef: ...

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult: ...

    # Не async def: это asynchronous generator, а не корутина, возвращающая
    # итератор — вызывающий код сразу делает `async for ... in gateway.get_library()`.
    def get_library(self) -> AsyncIterator[TrackCandidate]: ...

    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult: ...

    def library_insert_order(self) -> InsertOrder: ...

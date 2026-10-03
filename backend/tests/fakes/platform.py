from collections.abc import AsyncIterator, Sequence

from syncplaylists.shared_kernel.application.ports import AccountAccess
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


class FakeMusicPlatformGateway:
    def __init__(
        self,
        platform: Platform = Platform.SPOTIFY,
        search_results: list[TrackCandidate] | None = None,
        isrc_results: dict[str, list[TrackCandidate]] | None = None,
        playlist: PlaylistSnapshot | None = None,
        library: list[TrackCandidate] | None = None,
        insert_order: InsertOrder = InsertOrder.BOTTOM,
    ) -> None:
        self.platform = platform
        self._search_results = search_results or []
        self._isrc_results = isrc_results or {}
        self._playlist = playlist
        self._library = library or []
        self._insert_order = insert_order
        self.search_calls = 0
        self.search_by_isrc_calls = 0
        self.created_playlists: list[tuple[str, str | None]] = []
        self.added_to_playlist: list[ExternalTrackRef] = []
        self.added_to_library: list[ExternalTrackRef] = []

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        self.search_calls += 1
        return list(self._search_results[:limit])

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]:
        self.search_by_isrc_calls += 1
        return list(self._isrc_results.get(isrc.value, []))

    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot:
        assert self._playlist is not None, "у фейка не настроен плейлист-источник"
        return self._playlist

    async def create_playlist(self, title: str, description: str | None) -> PlaylistRef:
        self.created_playlists.append((title, description))
        return PlaylistRef(self.platform, f"created-{len(self.created_playlists)}")

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult:
        self.added_to_playlist.extend(tracks)
        return AddResult(added=tuple(tracks), failed=())

    async def get_library(self) -> AsyncIterator[TrackCandidate]:
        for track in self._library:
            yield track

    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult:
        self.added_to_library.extend(tracks)
        return AddResult(added=tuple(tracks), failed=())

    def library_insert_order(self) -> InsertOrder:
        return self._insert_order


class FakeGatewayFactory:
    def __init__(self, gateways: dict[Platform, FakeMusicPlatformGateway] | None = None) -> None:
        self._gateways = gateways or {}
        self.accesses: list[AccountAccess] = []

    def register(self, gateway: FakeMusicPlatformGateway) -> None:
        self._gateways[gateway.platform] = gateway

    def for_account(self, access: AccountAccess) -> FakeMusicPlatformGateway:
        self.accesses.append(access)
        return self._gateways[access.platform]

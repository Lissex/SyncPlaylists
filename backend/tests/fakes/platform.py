from collections.abc import AsyncIterator, Callable, Sequence

from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import PlatformNotSupportedError
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


class FakeMusicPlatformGateway:
    def __init__(
        self,
        platform: Platform = Platform.SPOTIFY,
        search_results: list[TrackCandidate] | None = None,
        isrc_results: dict[str, list[TrackCandidate]] | None = None,
        playlist: PlaylistSnapshot | None = None,
        library: list[TrackCandidate] | None = None,
        insert_order: InsertOrder = InsertOrder.BOTTOM,
        search_fn: Callable[[TrackQuery], list[TrackCandidate]] | None = None,
        playlist_owner: str | None = None,
        own_library_refs: set[PlaylistRef] | None = None,
        playlist_capacity: int | None = None,
    ) -> None:
        self.platform = platform
        self._search_results = search_results or []
        self._search_fn = search_fn
        self._isrc_results = isrc_results or {}
        self._playlist = playlist
        self._library = library or []
        self._insert_order = insert_order
        # По умолчанию плейлист «свой» для make_access() из tests.fakes.accounts.
        self._playlist_owner = playlist_owner or f"{platform.value}-owner"
        self._own_library_refs = own_library_refs or set()
        self._playlist_capacity = playlist_capacity
        # Имя метода → исключение, которое он бросит (ошибки площадки в тестах).
        self.failures: dict[str, Exception] = {}
        self.search_calls = 0
        self.search_queries: list[TrackQuery] = []
        self.search_by_isrc_calls = 0
        self.playlist_info_calls = 0
        self.is_own_library_calls = 0
        self.created_playlists: list[tuple[str, str | None]] = []
        self.create_request_ids: list[str | None] = []
        self.added_to_playlist: list[ExternalTrackRef] = []
        self.playlist_contents: dict[PlaylistRef, list[ExternalTrackRef]] = {}
        self.added_to_library: list[ExternalTrackRef] = []

    def _maybe_fail(self, method: str) -> None:
        if method in self.failures:
            raise self.failures[method]

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        self._maybe_fail("search")
        self.search_calls += 1
        self.search_queries.append(query)
        results = self._search_fn(query) if self._search_fn else self._search_results
        return list(results[:limit])

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]:
        self._maybe_fail("search_by_isrc")
        self.search_by_isrc_calls += 1
        return list(self._isrc_results.get(isrc.value, []))

    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot:
        self._maybe_fail("get_playlist")
        assert self._playlist is not None, "у фейка не настроен плейлист-источник"
        return self._playlist

    async def playlist_info(self, ref: PlaylistRef) -> PlaylistInfo:
        self._maybe_fail("playlist_info")
        self.playlist_info_calls += 1
        return PlaylistInfo(
            ref=ref,
            title=self._playlist.title if self._playlist else "Fake playlist",
            description=None,
            owner_external_id=self._playlist_owner,
            track_count=len(self._playlist.tracks) if self._playlist else 0,
        )

    async def is_own_library(self, ref: PlaylistRef) -> bool:
        self.is_own_library_calls += 1
        return ref in self._own_library_refs

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> PlaylistRef:
        self._maybe_fail("create_playlist")
        self.created_playlists.append((title, description))
        self.create_request_ids.append(request_id)
        return PlaylistRef(self.platform, f"created-{len(self.created_playlists)}")

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult:
        self._maybe_fail("add_tracks")
        self.added_to_playlist.extend(tracks)
        self.playlist_contents.setdefault(playlist, []).extend(tracks)
        return AddResult(added=tuple(tracks), failed=())

    async def get_library(self) -> AsyncIterator[TrackCandidate]:
        self._maybe_fail("get_library")
        for track in self._library:
            yield track

    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult:
        self._maybe_fail("add_to_library")
        self.added_to_library.extend(tracks)
        return AddResult(added=tuple(tracks), failed=())

    def library_insert_order(self) -> InsertOrder:
        return self._insert_order

    def playlist_capacity(self) -> int | None:
        return self._playlist_capacity


class FakeGatewayFactory:
    """Незарегистрированная площадка получает пустой фейк-шлюз; `unsupported` —
    площадки «без адаптера»."""

    def __init__(
        self,
        gateways: dict[Platform, FakeMusicPlatformGateway] | None = None,
        unsupported: set[Platform] | None = None,
    ) -> None:
        self._gateways = gateways or {}
        self.unsupported = unsupported or set()
        self.accesses: list[AccountAccess] = []

    def register(self, gateway: FakeMusicPlatformGateway) -> None:
        self._gateways[gateway.platform] = gateway

    def supports(self, platform: Platform) -> bool:
        return platform not in self.unsupported

    def for_account(self, access: AccountAccess) -> FakeMusicPlatformGateway:
        if access.platform in self.unsupported:
            raise PlatformNotSupportedError(access.platform)
        self.accesses.append(access)
        if access.platform not in self._gateways:
            self._gateways[access.platform] = FakeMusicPlatformGateway(platform=access.platform)
        return self._gateways[access.platform]

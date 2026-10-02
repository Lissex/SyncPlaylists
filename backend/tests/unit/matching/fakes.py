from syncplaylists.modules.matching.domain.entities import TrackMatch
from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import ISRC, ExternalTrackRef, Platform


class FakeMusicPlatformGateway:
    def __init__(
        self,
        platform: Platform = Platform.SPOTIFY,
        search_results: list[TrackCandidate] | None = None,
        isrc_results: dict[str, list[TrackCandidate]] | None = None,
    ) -> None:
        self.platform = platform
        self._search_results = search_results or []
        self._isrc_results = isrc_results or {}
        self.search_calls = 0
        self.search_by_isrc_calls = 0

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        self.search_calls += 1
        return list(self._search_results[:limit])

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]:
        self.search_by_isrc_calls += 1
        return list(self._isrc_results.get(isrc.value, []))


class FakeTrackMatchRepository:
    def __init__(self) -> None:
        self._storage: dict[tuple[ExternalTrackRef, Platform], TrackMatch] = {}
        self.save_calls: list[TrackMatch] = []

    async def find(
        self, source_ref: ExternalTrackRef, target_platform: Platform
    ) -> TrackMatch | None:
        return self._storage.get((source_ref, target_platform))

    async def save(self, match: TrackMatch) -> None:
        self.save_calls.append(match)
        self._storage[(match.source_ref, match.target_platform)] = match

    async def record_confirmation(
        self, source_ref: ExternalTrackRef, target_platform: Platform
    ) -> None:
        match = self._storage.get((source_ref, target_platform))
        if match is not None:
            match.confirm()

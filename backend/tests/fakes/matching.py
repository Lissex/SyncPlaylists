from syncplaylists.modules.matching.domain.entities import TrackMatch
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform


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

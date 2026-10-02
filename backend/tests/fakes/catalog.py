from uuid import UUID

from syncplaylists.modules.catalog.domain.entities import CanonicalTrack, PlatformTrack
from syncplaylists.shared_kernel.domain.value_objects import ISRC, ExternalTrackRef, Platform


class FakePlatformTrackRepository:
    def __init__(self) -> None:
        self._by_ref: dict[tuple[Platform, str], PlatformTrack] = {}

    async def find_by_ref(self, ref: ExternalTrackRef) -> PlatformTrack | None:
        return self._by_ref.get((ref.platform, ref.external_id))

    async def find_by_id(self, pt_id: UUID) -> PlatformTrack | None:
        return next((t for t in self._by_ref.values() if t.id == pt_id), None)

    async def get_or_create(self, candidate: PlatformTrack) -> PlatformTrack:
        key = (candidate.platform, candidate.external_id)
        if key not in self._by_ref:
            self._by_ref[key] = candidate
        return self._by_ref[key]


class FakeCanonicalTrackRepository:
    def __init__(self) -> None:
        self._by_isrc: dict[str, CanonicalTrack] = {}

    async def find_by_isrc(self, isrc: ISRC) -> CanonicalTrack | None:
        return self._by_isrc.get(isrc.value)

    async def get_or_create_by_isrc(self, candidate: CanonicalTrack) -> CanonicalTrack:
        assert candidate.isrc is not None
        key = candidate.isrc.value
        if key not in self._by_isrc:
            self._by_isrc[key] = candidate
        return self._by_isrc[key]

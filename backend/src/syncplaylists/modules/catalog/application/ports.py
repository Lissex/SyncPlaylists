from typing import Protocol
from uuid import UUID

from syncplaylists.modules.catalog.domain.entities import CanonicalTrack, PlatformTrack
from syncplaylists.shared_kernel.domain.value_objects import ISRC, ExternalTrackRef


class PlatformTrackRepository(Protocol):
    async def find_by_ref(self, ref: ExternalTrackRef) -> PlatformTrack | None: ...

    async def find_by_id(self, pt_id: UUID) -> PlatformTrack | None: ...

    # Атомарный get-or-create (INSERT ... ON CONFLICT DO NOTHING + SELECT) — несколько
    # ARQ-джоб (`run_match`) могут одновременно резолвить один и тот же ExternalTrackRef,
    # find-затем-save гонку не закрывает.
    async def get_or_create(self, candidate: PlatformTrack) -> PlatformTrack: ...


class CanonicalTrackRepository(Protocol):
    async def find_by_isrc(self, isrc: ISRC) -> CanonicalTrack | None: ...

    async def get_or_create_by_isrc(self, candidate: CanonicalTrack) -> CanonicalTrack: ...

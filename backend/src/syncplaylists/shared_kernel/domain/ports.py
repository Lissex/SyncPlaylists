from typing import Protocol

from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import ISRC, Platform


class MusicPlatformGateway(Protocol):
    platform: Platform

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]: ...

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]: ...

    # get_playlist / create_playlist / add_tracks / get_library / add_to_library /
    # library_insert_order добавятся на этапах 3-4 (transfers / адаптеры площадок).

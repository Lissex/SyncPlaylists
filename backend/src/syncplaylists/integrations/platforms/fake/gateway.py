from collections.abc import AsyncIterator, Sequence
from uuid import uuid4

from syncplaylists.integrations.platforms.fake.catalog import DEMO_TRACKS, DemoTrack
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
    Duration,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)


class FakeMusicPlatformGateway:
    """Единственный реальный (не тестовый) адаптер на этом этапе — настоящих
    интеграций с площадками ещё нет (этап 4). Отдаёт один и тот же демо-каталог
    для любого плейлиста/медиатеки; external_id детерминирован от платформы и слага,
    так что перенос между двумя инстансами этого гейтвея находит совпадения по ISRC.
    Ничего не персистирует: add_tracks/add_to_library только отвечают "успех".
    """

    def __init__(self, platform: Platform, owner_external_id: str) -> None:
        self.platform = platform
        # Любой плейлист фейка «принадлежит» аккаунту, от имени которого шлюз создан:
        # проверка права записи в ExistingPlaylist проходит.
        self._owner_external_id = owner_external_id

    def _candidate(self, track: DemoTrack) -> TrackCandidate:
        return TrackCandidate(
            ref=ExternalTrackRef(self.platform, f"{self.platform.value}-{track.slug}"),
            title=track.title,
            artist=track.artist,
            duration=Duration(track.duration_ms),
            isrc=ISRC(track.isrc),
        )

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        if query.isrc is not None:
            return await self.search_by_isrc(query.isrc)
        matches = [
            self._candidate(t) for t in DEMO_TRACKS if t.title.lower() == query.title.lower()
        ]
        return matches[:limit]

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]:
        return [self._candidate(t) for t in DEMO_TRACKS if t.isrc == isrc.value]

    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot:
        return PlaylistSnapshot(
            ref=ref,
            title="Demo playlist",
            description=None,
            tracks=tuple(self._candidate(t) for t in DEMO_TRACKS),
        )

    async def playlist_info(self, ref: PlaylistRef) -> PlaylistInfo:
        return PlaylistInfo(
            ref=ref,
            title="Demo playlist",
            description=None,
            owner_external_id=self._owner_external_id,
            track_count=len(DEMO_TRACKS),
        )

    async def is_own_library(self, ref: PlaylistRef) -> bool:
        return False

    async def create_playlist(self, title: str, description: str | None) -> PlaylistRef:
        return PlaylistRef(self.platform, f"created-{uuid4().hex[:8]}")

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult:
        return AddResult(added=tuple(tracks), failed=())

    async def get_library(self) -> AsyncIterator[TrackCandidate]:
        for track in DEMO_TRACKS:
            yield self._candidate(track)

    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult:
        return AddResult(added=tuple(tracks), failed=())

    def library_insert_order(self) -> InsertOrder:
        return InsertOrder.TOP

    def playlist_capacity(self) -> int | None:
        return None

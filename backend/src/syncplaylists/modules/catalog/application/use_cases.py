from uuid import uuid4

from syncplaylists.modules.catalog.application.ports import (
    CanonicalTrackRepository,
    PlatformTrackRepository,
)
from syncplaylists.modules.catalog.domain.entities import CanonicalTrack, PlatformTrack
from syncplaylists.shared_kernel.domain.value_objects import ISRC, Duration, ExternalTrackRef


def _normalize(text: str) -> str:
    return text.strip().lower()


class EnsurePlatformTrackUseCase:
    """Единственная публичная точка входа, которой другие контексты резолвят
    ExternalTrackRef в platform_track.id. Полноценная нормализация/дедуп по
    ISRC-похожести — задача library_tools (этап 9); здесь только get-or-create.
    """

    def __init__(
        self,
        platform_tracks: PlatformTrackRepository,
        canonical_tracks: CanonicalTrackRepository,
    ) -> None:
        self._platform_tracks = platform_tracks
        self._canonical_tracks = canonical_tracks

    async def execute(
        self,
        ref: ExternalTrackRef,
        title: str,
        artist: str,
        duration: Duration | None = None,
        isrc: ISRC | None = None,
    ) -> PlatformTrack:
        existing = await self._platform_tracks.find_by_ref(ref)
        if existing is not None:
            return existing

        canonical_id = None
        if isrc is not None:
            canonical_candidate = CanonicalTrack(
                id=uuid4(),
                title_norm=_normalize(title),
                artist_norm=_normalize(artist),
                duration=duration,
                isrc=isrc,
            )
            canonical = await self._canonical_tracks.get_or_create_by_isrc(canonical_candidate)
            canonical_id = canonical.id

        candidate = PlatformTrack(
            id=uuid4(),
            platform=ref.platform,
            external_id=ref.external_id,
            raw_title=title,
            raw_artist=artist,
            duration=duration,
            isrc=isrc,
            canonical_id=canonical_id,
        )
        return await self._platform_tracks.get_or_create(candidate)

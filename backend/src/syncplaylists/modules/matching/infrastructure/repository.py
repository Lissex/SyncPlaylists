from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.application.ports import PlatformTrackRepository
from syncplaylists.modules.matching.domain.entities import TrackMatch
from syncplaylists.modules.matching.infrastructure.mappers import match_to_domain, match_to_orm
from syncplaylists.modules.matching.infrastructure.orm import TrackMatchOrm
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform


class SqlTrackMatchRepository:
    def __init__(self, session: AsyncSession, platform_tracks: PlatformTrackRepository) -> None:
        self._session = session
        self._platform_tracks = platform_tracks

    async def find(
        self, source_ref: ExternalTrackRef, target_platform: Platform
    ) -> TrackMatch | None:
        source = await self._platform_tracks.find_by_ref(source_ref)
        if source is None:
            return None
        orm = await self._session.scalar(
            select(TrackMatchOrm).where(
                TrackMatchOrm.source_pt_id == source.id,
                TrackMatchOrm.target_platform == target_platform.value,
            )
        )
        return await match_to_domain(orm, self._platform_tracks) if orm is not None else None

    async def save(self, match: TrackMatch) -> None:
        orm = await match_to_orm(match, self._platform_tracks)
        await self._session.merge(orm)

    async def record_confirmation(
        self, source_ref: ExternalTrackRef, target_platform: Platform
    ) -> None:
        match = await self.find(source_ref, target_platform)
        if match is None:
            return
        match.confirm()
        orm = await match_to_orm(match, self._platform_tracks)
        await self._session.merge(orm)

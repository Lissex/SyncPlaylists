from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.domain.entities import CanonicalTrack, PlatformTrack
from syncplaylists.modules.catalog.infrastructure.mappers import (
    canonical_to_domain,
    canonical_to_orm,
    platform_track_to_domain,
    platform_track_to_orm,
)
from syncplaylists.modules.catalog.infrastructure.orm import CanonicalTrackOrm, PlatformTrackOrm
from syncplaylists.shared_kernel.domain.value_objects import ISRC, ExternalTrackRef


class SqlPlatformTrackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_ref(self, ref: ExternalTrackRef) -> PlatformTrack | None:
        orm = await self._session.scalar(
            select(PlatformTrackOrm).where(
                PlatformTrackOrm.platform == ref.platform.value,
                PlatformTrackOrm.external_id == ref.external_id,
            )
        )
        return platform_track_to_domain(orm) if orm is not None else None

    async def find_by_id(self, pt_id: UUID) -> PlatformTrack | None:
        orm = await self._session.get(PlatformTrackOrm, pt_id)
        return platform_track_to_domain(orm) if orm is not None else None

    async def get_or_create(self, candidate: PlatformTrack) -> PlatformTrack:
        values = platform_track_to_orm(candidate)
        stmt = (
            insert(PlatformTrackOrm)
            .values(
                id=values.id,
                platform=values.platform,
                external_id=values.external_id,
                canonical_id=values.canonical_id,
                raw_title=values.raw_title,
                raw_artist=values.raw_artist,
                duration_ms=values.duration_ms,
                isrc=values.isrc,
            )
            .on_conflict_do_nothing(index_elements=["platform", "external_id"])
        )
        await self._session.execute(stmt)
        existing = await self.find_by_ref(
            ExternalTrackRef(candidate.platform, candidate.external_id)
        )
        assert existing is not None  # INSERT либо прошёл, либо конфликтная строка уже есть
        return existing


class SqlCanonicalTrackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_isrc(self, isrc: ISRC) -> CanonicalTrack | None:
        orm = await self._session.scalar(
            select(CanonicalTrackOrm).where(CanonicalTrackOrm.isrc == isrc.value)
        )
        return canonical_to_domain(orm) if orm is not None else None

    async def get_or_create_by_isrc(self, candidate: CanonicalTrack) -> CanonicalTrack:
        values = canonical_to_orm(candidate)
        stmt = (
            insert(CanonicalTrackOrm)
            .values(
                id=values.id,
                isrc=values.isrc,
                title_norm=values.title_norm,
                artist_norm=values.artist_norm,
                duration_ms=values.duration_ms,
                mbid=values.mbid,
            )
            .on_conflict_do_nothing(index_elements=["isrc"])
        )
        await self._session.execute(stmt)
        assert candidate.isrc is not None
        existing = await self.find_by_isrc(candidate.isrc)
        assert existing is not None
        return existing

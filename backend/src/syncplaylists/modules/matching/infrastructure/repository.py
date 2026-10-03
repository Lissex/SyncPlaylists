from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
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
            select(TrackMatchOrm)
            .where(
                TrackMatchOrm.source_pt_id == source.id,
                TrackMatchOrm.target_platform == target_platform.value,
            )
            # Строку меняют Core-UPDATE'ы (record_confirmation) мимо identity map.
            .execution_options(populate_existing=True)
        )
        return await match_to_domain(orm, self._platform_tracks) if orm is not None else None

    async def save(self, match: TrackMatch) -> TrackMatch:
        # Не merge() с новым UUID: track_matches — глобальный кэш с UNIQUE(source_pt_id,
        # target_platform), и тот же трек параллельно (или позже) матчат другие переносы.
        # INSERT ... ON CONFLICT DO NOTHING ждёт коммита конкурирующей вставки и не падает;
        # затем читаем строку, которая реально лежит в кэше (свою или чужую).
        values = await match_to_orm(match, self._platform_tracks)
        await self._session.execute(
            insert(TrackMatchOrm)
            .values(
                id=values.id,
                source_pt_id=values.source_pt_id,
                target_platform=values.target_platform,
                target_pt_id=values.target_pt_id,
                method=values.method,
                score=values.score,
                confirmations=values.confirmations,
            )
            .on_conflict_do_nothing(index_elements=["source_pt_id", "target_platform"])
        )
        stored = await self._session.scalar(
            select(TrackMatchOrm)
            .where(
                TrackMatchOrm.source_pt_id == values.source_pt_id,
                TrackMatchOrm.target_platform == values.target_platform,
            )
            # Строка могла быть уже в identity map сессии (find() до вставки) — без
            # populate_existing вернулся бы устаревший объект.
            .execution_options(populate_existing=True)
        )
        assert stored is not None  # INSERT прошёл или конфликтная строка уже закоммичена
        return await match_to_domain(stored, self._platform_tracks)

    async def record_confirmation(
        self, source_ref: ExternalTrackRef, target_platform: Platform
    ) -> None:
        source = await self._platform_tracks.find_by_ref(source_ref)
        if source is None:
            return
        await self._session.execute(
            update(TrackMatchOrm)
            .where(
                TrackMatchOrm.source_pt_id == source.id,
                TrackMatchOrm.target_platform == target_platform.value,
            )
            .values(confirmations=TrackMatchOrm.confirmations + 1)
        )

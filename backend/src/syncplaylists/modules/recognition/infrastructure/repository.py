from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.application.ports import PlatformTrackRepository
from syncplaylists.modules.recognition.domain.entities import Recognition
from syncplaylists.modules.recognition.infrastructure.mappers import (
    recognition_to_domain,
    recognition_values,
)
from syncplaylists.modules.recognition.infrastructure.orm import RecognitionOrm
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef


class SqlRecognitionRepository:
    def __init__(self, session: AsyncSession, platform_tracks: PlatformTrackRepository) -> None:
        self._session = session
        self._platform_tracks = platform_tracks

    async def find(self, source_ref: ExternalTrackRef) -> Recognition | None:
        source = await self._platform_tracks.find_by_ref(source_ref)
        if source is None:
            return None
        orm = await self._session.scalar(
            select(RecognitionOrm)
            .where(RecognitionOrm.source_pt_id == source.id)
            # Строку меняет Core-upsert (save) мимо identity map.
            .execution_options(populate_existing=True)
        )
        return recognition_to_domain(orm, source_ref) if orm is not None else None

    async def save(self, recognition: Recognition) -> None:
        source = await self._platform_tracks.find_by_ref(recognition.source_ref)
        assert source is not None, (
            f"platform_track для {recognition.source_ref} должен существовать"
        )
        values = recognition_values(recognition, source.id)
        # Одна строка на трек: новое распознавание заменяет прежнее (устаревший
        # отрицательный исход — свежим). id строки сохраняется прежний.
        updates = {k: v for k, v in values.items() if k not in ("id", "source_pt_id")}
        await self._session.execute(
            insert(RecognitionOrm)
            .values(**values)
            .on_conflict_do_update(index_elements=["source_pt_id"], set_=updates)
        )

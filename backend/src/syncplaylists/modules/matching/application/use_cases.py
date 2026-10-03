import dataclasses

from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.matching.domain.entities import MatchMethod
from syncplaylists.modules.matching.domain.pipeline import (
    MatchAttempt,
    MatchingPipeline,
    MatchRequest,
    MatchStatus,
)
from syncplaylists.modules.matching.domain.ports import TrackMatchRepository
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import Platform


class ResolveTrackMatchUseCase:
    def __init__(
        self,
        pipeline: MatchingPipeline,
        repository: TrackMatchRepository,
        ensure_platform_track: EnsurePlatformTrackUseCase,
    ) -> None:
        self._pipeline = pipeline
        self._repository = repository
        self._ensure_platform_track = ensure_platform_track

    async def execute(self, source: TrackCandidate, target_platform: Platform) -> MatchAttempt:
        request = MatchRequest(source=source, target_platform=target_platform)
        attempt = await self._pipeline.run(request)
        if (
            attempt.status is MatchStatus.MATCHED
            and attempt.match is not None
            # Из кэша — уже в кэше; «трек сам себе» (одна площадка) кэшировать незачем.
            and attempt.method not in (MatchMethod.CACHE, MatchMethod.SAME_PLATFORM)
        ):
            target = next(c for c in attempt.candidates if c.ref == attempt.match.target_ref)
            # track_matches ссылается на platform_tracks по id — обе стороны матча
            # (source и найденный на target_platform трек) должны там существовать
            # до вставки строки матча.
            await self._ensure_platform_track.execute(
                source.ref, source.title, source.artist, source.duration, source.isrc
            )
            await self._ensure_platform_track.execute(
                target.ref, target.title, target.artist, target.duration, target.isrc
            )
            stored = await self._repository.save(attempt.match)
            if stored.id != attempt.match.id:
                # Кэш уже содержал матч (записал другой перенос/параллельная джоба) —
                # используем его, чтобы все переносы видели одно соответствие.
                attempt = dataclasses.replace(attempt, match=stored)
        return attempt

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
            and attempt.method is not MatchMethod.CACHE
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
            await self._repository.save(attempt.match)
        return attempt

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
    def __init__(self, pipeline: MatchingPipeline, repository: TrackMatchRepository) -> None:
        self._pipeline = pipeline
        self._repository = repository

    async def execute(self, source: TrackCandidate, target_platform: Platform) -> MatchAttempt:
        request = MatchRequest(source=source, target_platform=target_platform)
        attempt = await self._pipeline.run(request)
        if (
            attempt.status is MatchStatus.MATCHED
            and attempt.match is not None
            and attempt.method is not MatchMethod.CACHE
        ):
            await self._repository.save(attempt.match)
        return attempt

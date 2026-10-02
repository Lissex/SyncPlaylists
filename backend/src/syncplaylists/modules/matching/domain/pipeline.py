from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.shared_kernel.domain.base import ValueObject
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import Platform


class MatchStatus(StrEnum):
    MATCHED = "matched"
    UNCERTAIN = "uncertain"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class MatchRequest(ValueObject):
    source: TrackCandidate
    target_platform: Platform


@dataclass(frozen=True, slots=True)
class MatchAttempt(ValueObject):
    status: MatchStatus
    match: TrackMatch | None = None
    candidates: tuple[TrackCandidate, ...] = ()
    method: MatchMethod | None = None


class MatchStrategy(Protocol):
    async def attempt(self, request: MatchRequest) -> MatchAttempt | None: ...

    # None = стратегия неприменима, передать запрос дальше по цепочке.


class MatchingPipeline:
    def __init__(self, strategies: Sequence[MatchStrategy]) -> None:
        self._strategies = tuple(strategies)

    async def run(self, request: MatchRequest) -> MatchAttempt:
        for strategy in self._strategies:
            attempt = await strategy.attempt(request)
            if attempt is not None:
                return attempt
        return MatchAttempt(status=MatchStatus.NOT_FOUND)

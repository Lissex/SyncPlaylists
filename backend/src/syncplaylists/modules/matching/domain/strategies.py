from uuid import uuid4

from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import (
    MatchAttempt,
    MatchingPipeline,
    MatchRequest,
    MatchStatus,
)
from syncplaylists.modules.matching.domain.ports import TrackMatchRepository
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.version import (
    VersionInfo,
    VersionTag,
    version_search_suffix,
    versions_match,
)
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import MatchScore, MatchTier


class CacheStrategy:
    def __init__(self, repository: TrackMatchRepository) -> None:
        self._repository = repository

    async def attempt(self, request: MatchRequest) -> MatchAttempt | None:
        match = await self._repository.find(request.source.ref, request.target_platform)
        if match is None:
            return None
        return MatchAttempt(status=MatchStatus.MATCHED, match=match, method=MatchMethod.CACHE)


class IsrcStrategy:
    def __init__(self, gateway: MusicPlatformGateway) -> None:
        self._gateway = gateway

    async def attempt(self, request: MatchRequest) -> MatchAttempt | None:
        isrc = request.source.isrc
        if isrc is None:
            return None
        candidates = await self._gateway.search_by_isrc(isrc)
        if not candidates:
            return None
        best = candidates[0]
        match = TrackMatch(
            id=uuid4(),
            source_ref=request.source.ref,
            target_platform=request.target_platform,
            target_ref=best.ref,
            method=MatchMethod.ISRC,
            score=MatchScore(1.0),
        )
        return MatchAttempt(
            status=MatchStatus.MATCHED,
            match=match,
            candidates=tuple(candidates),
            method=MatchMethod.ISRC,
        )


class FuzzySearchStrategy:
    def __init__(
        self,
        gateway: MusicPlatformGateway,
        normalizer: TrackNormalizer,
        scorer: MatchScorer,
        search_limit: int = 10,
    ) -> None:
        self._gateway = gateway
        self._normalizer = normalizer
        self._scorer = scorer
        self._search_limit = search_limit

    async def attempt(self, request: MatchRequest) -> MatchAttempt | None:
        source = request.source
        query = TrackQuery(
            title=source.title, artist=source.artist, isrc=source.isrc, duration=source.duration
        )
        candidates = await self._gateway.search(query, limit=self._search_limit)

        source_variants = self._normalizer.variants(source.title, source.artist)
        source_version = source_variants[0].version  # одна на все варианты (см. variants)
        if source_version.tag is not VersionTag.ORIGINAL and not any(
            versions_match(source_version, self._version_of(c)) for c in candidates
        ):
            # Источник — live/ремикс/..., а первый поиск той же версии не нашёл. Сначала
            # ищем её отдельным запросом и только потом соглашаемся на оригинал — его
            # скорер всё равно ограничит потолком ниже AUTO (UNCERTAIN → ручной выбор),
            # а не возьмёт молча (ARCHITECTURE.md, раздел 7).
            candidates = await self._search_same_version(
                source, source_variants[0].title, source_version, candidates
            )
        if not candidates:
            return MatchAttempt(status=MatchStatus.NOT_FOUND)

        scored = [
            (
                self._scorer.best_score(
                    source_variants,
                    source.duration,
                    self._normalizer.variants(candidate.title, candidate.artist),
                    candidate.duration,
                ),
                candidate,
            )
            for candidate in candidates
        ]
        best_score, best_candidate = max(scored, key=lambda pair: pair[0].value)
        all_candidates = tuple(candidate for _, candidate in scored)

        # TODO(этап 6): когда появится AudioRecognitionStrategy после этой стратегии,
        # UNCERTAIN/NOT_FOUND отсюда должны стать None (передать дальше), а не терминальными.
        if best_score.tier is MatchTier.NOT_FOUND:
            return MatchAttempt(status=MatchStatus.NOT_FOUND, candidates=all_candidates)

        match = TrackMatch(
            id=uuid4(),
            source_ref=source.ref,
            target_platform=request.target_platform,
            target_ref=best_candidate.ref,
            method=MatchMethod.FUZZY,
            score=best_score,
        )
        if best_score.tier is MatchTier.AUTO:
            return MatchAttempt(
                status=MatchStatus.MATCHED,
                match=match,
                candidates=all_candidates,
                method=MatchMethod.FUZZY,
            )
        return MatchAttempt(
            status=MatchStatus.UNCERTAIN,
            candidates=all_candidates,
            method=MatchMethod.FUZZY,
        )

    def _version_of(self, candidate: TrackCandidate) -> VersionInfo:
        return self._normalizer.variants(candidate.title, candidate.artist)[0].version

    async def _search_same_version(
        self,
        source: TrackCandidate,
        clean_title: str,
        version: VersionInfo,
        found: list[TrackCandidate],
    ) -> list[TrackCandidate]:
        suffix = version_search_suffix(version)
        if suffix is None:
            return found
        query = TrackQuery(
            title=f"{clean_title} {suffix}", artist=source.artist, duration=source.duration
        )
        extra = await self._gateway.search(query, limit=self._search_limit)
        seen = {candidate.ref for candidate in found}
        # Найденные вторым запросом — первыми: при ручном выборе пользователь сначала
        # видит кандидатов нужной версии.
        return [c for c in extra if c.ref not in seen] + found


def build_default_pipeline(
    gateway: MusicPlatformGateway,
    repository: TrackMatchRepository,
    normalizer: TrackNormalizer,
    scorer: MatchScorer,
    search_limit: int = 10,
) -> MatchingPipeline:
    return MatchingPipeline(
        [
            CacheStrategy(repository),
            IsrcStrategy(gateway),
            FuzzySearchStrategy(gateway, normalizer, scorer, search_limit),
        ]
    )

import dataclasses
from collections.abc import Sequence
from uuid import uuid4

from syncplaylists.modules.matching.domain.artists import parse_artist_names
from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.modules.matching.domain.normalization import NormalizedTrack, TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import (
    MatchAttempt,
    MatchingPipeline,
    MatchRequest,
    MatchStatus,
)
from syncplaylists.modules.matching.domain.ports import TrackMatchRepository
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.upload_trust import UploadTrust
from syncplaylists.modules.matching.domain.version import (
    VersionInfo,
    version_search_suffix,
)
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import MatchScore, MatchTier


class SamePlatformStrategy:
    """Перенос внутри одной площадки (чужой плейлист к себе, лайки в плейлист, ...):
    id трека источника годится и для назначения — искать нечего. Ноль запросов к
    площадке вместо 1–2 поисков на трек и без ложных UNCERTAIN."""

    async def attempt(self, request: MatchRequest) -> MatchAttempt | None:
        source = request.source
        if source.ref.platform is not request.target_platform:
            return None
        match = TrackMatch(
            id=uuid4(),
            source_ref=source.ref,
            target_platform=request.target_platform,
            target_ref=source.ref,
            method=MatchMethod.SAME_PLATFORM,
            score=MatchScore(1.0),
        )
        return MatchAttempt(
            status=MatchStatus.MATCHED,
            match=match,
            candidates=(source,),
            method=MatchMethod.SAME_PLATFORM,
        )


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
            restriction=best.restriction,
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
        upload_trust: UploadTrust | None = None,
    ) -> None:
        self._gateway = gateway
        self._normalizer = normalizer
        self._scorer = scorer
        self._search_limit = search_limit
        self._upload_trust = upload_trust or UploadTrust()

    async def attempt(self, request: MatchRequest) -> MatchAttempt | None:
        source = request.source
        source_variants = self._normalizer.variants(source.title, source.artist)
        source_version = source_variants[0].version  # одна на все варианты (см. variants)
        query = self._query(source)
        candidates = await self._gateway.search(query, limit=self._search_limit)

        if not candidates:
            return MatchAttempt(status=MatchStatus.NOT_FOUND)

        source_artists = self._source_artists(source_variants)
        scored = []
        for candidate in candidates:
            verdict = self._upload_trust.classify(candidate, source_artists)
            candidate_variants = self._normalizer.variants(candidate.title, candidate.artist)
            if verdict.uploader_artist is not None:
                candidate_variants.append(
                    dataclasses.replace(candidate_variants[0], artist=verdict.uploader_artist)
                )
            score = self._scorer.best_score(
                source_variants, source.duration, candidate_variants, candidate.duration
            )
            # Бонус официальной заливке не должен поднять над потолком несовпадающую
            # версию (оригинал вместо live остаётся на ручное подтверждение).
            adjusted = self._scorer.cap_version_mismatch(
                self._upload_trust.adjust(score, verdict.kind),
                source_version,
                self._version_of(candidate),
            )
            scored.append((adjusted, verdict.kind, candidate))
        best_score, best_candidate = self._upload_trust.pick_best(scored)
        all_candidates = tuple(candidate for _, _, candidate in scored)

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
            restriction=best_candidate.restriction,
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

    @staticmethod
    def _source_artists(variants: Sequence[NormalizedTrack]) -> frozenset[str]:
        # Артист источника по всем вариантам разбора («Artist - Title» в названии,
        # поле артиста) — с чем сравнивать заливщика кандидата.
        names: set[str] = set()
        for variant in variants:
            names |= parse_artist_names(variant.artist)
        return frozenset(names)

    def _version_of(self, candidate: TrackCandidate) -> VersionInfo:
        return self._normalizer.variants(candidate.title, candidate.artist)[0].version

    def _query(self, source: TrackCandidate) -> TrackQuery:
        """Запрос к площадке — из разобранного источника, а не из сырых полей: у
        SoundCloud/VK поле артиста часто ник заливщика («Finesse Music»), а настоящий
        артист — в названии «Artist – Title»; мусор («(Prod Me)», «[FREE DL]») поиск
        площадки только сбивает (e2e 2026-10-03: пустая выдача). Версия — сразу в
        запросе («starboy kygo remix»): отдельный второй поиск «той же версии» стоил
        лишнего запроса к квоте площадки на каждый такой трек."""
        parsed = self._normalizer.normalize(source.title, source.artist)
        title = parsed.title or source.title
        suffix = version_search_suffix(parsed.version)
        if suffix:
            title = f"{title} {suffix}"
        return TrackQuery(
            title=title, artist=parsed.artist, isrc=source.isrc, duration=source.duration
        )


def build_default_pipeline(
    gateway: MusicPlatformGateway,
    repository: TrackMatchRepository,
    normalizer: TrackNormalizer,
    scorer: MatchScorer,
    search_limit: int = 10,
) -> MatchingPipeline:
    return MatchingPipeline(
        [
            SamePlatformStrategy(),
            CacheStrategy(repository),
            IsrcStrategy(gateway),
            FuzzySearchStrategy(gateway, normalizer, scorer, search_limit),
        ]
    )

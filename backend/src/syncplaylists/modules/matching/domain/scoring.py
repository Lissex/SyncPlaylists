from collections.abc import Sequence
from typing import ClassVar

from rapidfuzz import fuzz

from syncplaylists.modules.matching.domain.artists import artist_set_similarity, parse_artist_names
from syncplaylists.modules.matching.domain.normalization import NormalizedTrack, TrackNormalizer
from syncplaylists.modules.matching.domain.version import versions_match
from syncplaylists.shared_kernel.domain.value_objects import Duration, MatchScore


class MatchScorer:
    TITLE_WEIGHT: ClassVar[float] = 0.5
    ARTIST_WEIGHT: ClassVar[float] = 0.35
    DURATION_WEIGHT: ClassVar[float] = 0.15
    _DURATION_TOLERANCE_MS: ClassVar[int] = 3000
    _DURATION_ZERO_MS: ClassVar[int] = 15000
    # Чуть ниже порога AUTO — потолок, а не пол: плохое совпадение с несовпадающей
    # версией остаётся NOT_FOUND, а не подтягивается до UNCERTAIN.
    _VERSION_MISMATCH_CAP: ClassVar[float] = MatchScore.AUTO_THRESHOLD - 1e-9

    def __init__(self, normalizer: TrackNormalizer) -> None:
        self._normalizer = normalizer

    def score(
        self,
        source: NormalizedTrack,
        source_duration: Duration | None,
        candidate: NormalizedTrack,
        candidate_duration: Duration | None,
    ) -> MatchScore:
        source_lat = self._normalizer.latinize(source)
        candidate_lat = self._normalizer.latinize(candidate)
        title_sim = self._text_similarity(source_lat.title, candidate_lat.title)
        artist_sim = self._artist_similarity(source_lat.artist, candidate_lat.artist)

        if source_duration is None or candidate_duration is None:
            denom = self.TITLE_WEIGHT + self.ARTIST_WEIGHT
            total = (title_sim * self.TITLE_WEIGHT + artist_sim * self.ARTIST_WEIGHT) / denom
        else:
            duration_sim = self._duration_similarity(source_duration, candidate_duration)
            total = (
                title_sim * self.TITLE_WEIGHT
                + artist_sim * self.ARTIST_WEIGHT
                + duration_sim * self.DURATION_WEIGHT
            )

        if not versions_match(source_lat.version, candidate_lat.version):
            total = min(total, self._VERSION_MISMATCH_CAP)

        return MatchScore(value=min(1.0, max(0.0, total)))

    def best_score(
        self,
        source_variants: Sequence[NormalizedTrack],
        source_duration: Duration | None,
        candidate_variants: Sequence[NormalizedTrack],
        candidate_duration: Duration | None,
    ) -> MatchScore:
        return max(
            (
                self.score(source_variant, source_duration, candidate_variant, candidate_duration)
                for source_variant in source_variants
                for candidate_variant in candidate_variants
            ),
            key=lambda score: score.value,
        )

    def _text_similarity(self, a: str, b: str) -> float:
        if not a and not b:
            return 1.0
        return fuzz.token_set_ratio(a, b) / 100.0

    def _artist_similarity(self, a: str | None, b: str | None) -> float:
        return artist_set_similarity(parse_artist_names(a), parse_artist_names(b))

    def _duration_similarity(self, a: Duration, b: Duration) -> float:
        diff = abs(a.milliseconds - b.milliseconds)
        if diff <= self._DURATION_TOLERANCE_MS:
            return 1.0
        if diff >= self._DURATION_ZERO_MS:
            return 0.0
        span = self._DURATION_ZERO_MS - self._DURATION_TOLERANCE_MS
        return 1.0 - (diff - self._DURATION_TOLERANCE_MS) / span

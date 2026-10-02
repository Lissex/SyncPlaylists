from typing import ClassVar

from rapidfuzz import fuzz

from syncplaylists.modules.matching.domain.normalization import NormalizedTrack, TrackNormalizer
from syncplaylists.shared_kernel.domain.value_objects import Duration, MatchScore


class MatchScorer:
    TITLE_WEIGHT: ClassVar[float] = 0.5
    ARTIST_WEIGHT: ClassVar[float] = 0.35
    DURATION_WEIGHT: ClassVar[float] = 0.15
    # Вклад длительности, когда она неизвестна хотя бы с одной стороны —
    # не наказываем и не поощряем, просто не учитываем этот сигнал.
    _DURATION_NEUTRAL: ClassVar[float] = 0.5
    _DURATION_TOLERANCE_MS: ClassVar[int] = 3000
    _DURATION_FALLOFF_MS: ClassVar[float] = 60000.0

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
        artist_sim = self._text_similarity(source_lat.artist or "", candidate_lat.artist or "")
        duration_sim = self._duration_similarity(source_duration, candidate_duration)
        total = (
            title_sim * self.TITLE_WEIGHT
            + artist_sim * self.ARTIST_WEIGHT
            + duration_sim * self.DURATION_WEIGHT
        )
        return MatchScore(value=min(1.0, max(0.0, total)))

    def _text_similarity(self, a: str, b: str) -> float:
        if not a and not b:
            return 1.0
        return fuzz.token_set_ratio(a, b) / 100.0

    def _duration_similarity(self, a: Duration | None, b: Duration | None) -> float:
        if a is None or b is None:
            return self._DURATION_NEUTRAL
        if a.is_close_to(b, tolerance_ms=self._DURATION_TOLERANCE_MS):
            return 1.0
        diff = abs(a.milliseconds - b.milliseconds)
        return max(0.0, 1.0 - diff / self._DURATION_FALLOFF_MS)

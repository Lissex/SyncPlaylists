import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from syncplaylists.modules.matching.domain.transliteration import transliterate
from syncplaylists.shared_kernel.domain.base import ValueObject

# Мусор, который реально режется (feat./ft., remaster, Free DL, prod.) — не трогаем
# remix/live/cover и подобные пометки версии: они означают другую запись, и дальше
# это решает вес длительности в MatchScorer, а не нормализатор.
_JUNK_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\(\s*feat\.?[^)]*\)"),
    re.compile(r"\[\s*feat\.?[^\]]*\]"),
    re.compile(r"\bfeat\.?\s+[^\-([,]+"),
    re.compile(r"\bft\.?\s+[^\-([,]+"),
    re.compile(r"\(\s*remaster(?:ed)?[^)]*\)"),
    re.compile(r"\bremaster(?:ed)?\b"),
    re.compile(r"\(\s*free dl\s*\)"),
    re.compile(r"\bfree dl\b"),
    re.compile(r"\[\s*prod\.[^\]]*\]"),
    re.compile(r"\(\s*prod\.[^)]*\)"),
    re.compile(r"\bprod\.?\s+(?:by\s+)?\S+"),
)

_ARTIST_TITLE_PATTERN: Final = re.compile(r"^(?P<artist>.+?)\s[-–—]\s(?P<title>.+)$")
_WHITESPACE_PATTERN: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class NormalizedTrack(ValueObject):
    title: str
    artist: str | None


class TrackNormalizer:
    def normalize(self, raw_title: str, raw_artist: str | None = None) -> NormalizedTrack:
        cleaned_title = self._clean(raw_title)
        match = _ARTIST_TITLE_PATTERN.match(cleaned_title)
        if match:
            return NormalizedTrack(
                title=match.group("title").strip(),
                artist=match.group("artist").strip() or None,
            )
        cleaned_artist = self._clean(raw_artist) if raw_artist else None
        return NormalizedTrack(title=cleaned_title, artist=cleaned_artist or None)

    def latinize(self, track: NormalizedTrack) -> NormalizedTrack:
        return NormalizedTrack(
            title=transliterate(track.title),
            artist=transliterate(track.artist) if track.artist is not None else None,
        )

    def _clean(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        text = text.lower()
        text = text.replace("ё", "е")
        for pattern in _JUNK_PATTERNS:
            text = pattern.sub(" ", text)
        return _WHITESPACE_PATTERN.sub(" ", text).strip()

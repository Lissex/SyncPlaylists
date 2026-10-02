import re
import unicodedata
from dataclasses import dataclass, field
from typing import Final

from syncplaylists.modules.matching.domain.transliteration import transliterate
from syncplaylists.modules.matching.domain.version import VersionInfo, VersionTag, extract_version
from syncplaylists.shared_kernel.domain.base import ValueObject

# Мусор, который реально режется (feat./ft., remaster, Free DL, prod.) — не трогаем
# remix/live/cover и подобные пометки версии: те извлекаются отдельно в VersionInfo
# (см. version.py) ещё до этого шага.
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
    version: VersionInfo = field(default_factory=VersionInfo)


class TrackNormalizer:
    def normalize(self, raw_title: str, raw_artist: str | None = None) -> NormalizedTrack:
        cleaned_title, version = self._prepare_title(raw_title)
        match = _ARTIST_TITLE_PATTERN.match(cleaned_title)
        if match:
            return NormalizedTrack(
                title=match.group("title").strip(),
                artist=match.group("artist").strip() or None,
                version=version,
            )
        cleaned_artist = self._clean_artist_field(raw_artist) if raw_artist else None
        return NormalizedTrack(title=cleaned_title, artist=cleaned_artist or None, version=version)

    def variants(self, raw_title: str, raw_artist: str | None = None) -> list[NormalizedTrack]:
        cleaned_title, title_version = self._prepare_title(raw_title)
        swapped_title, artist_version = (
            self._prepare_title(raw_artist) if raw_artist else (None, VersionInfo())
        )
        # Версия — это факт о самой записи, а не о том, в каком поле мы её сейчас
        # считаем title. Если считать версию отдельно для каждого варианта (в т.ч. для
        # swapped, где раздел ролей title/artist перевёрнут), best_score() сможет
        # "обойти" потолок несовпадения версий, выбрав вариант, в котором маркер версии
        # просто не нашёлся (т.к. его искали не в том поле). Поэтому версия — одна на
        # все варианты одной стороны: берём там, где она реально нашлась.
        version = title_version if title_version.tag is not VersionTag.ORIGINAL else artist_version

        cleaned_artist = self._clean_artist_field(raw_artist) if raw_artist else None
        fallback = NormalizedTrack(
            title=cleaned_title, artist=cleaned_artist or None, version=version
        )
        results = [fallback]

        match = _ARTIST_TITLE_PATTERN.match(cleaned_title)
        if match:
            dash_variant = NormalizedTrack(
                title=match.group("title").strip(),
                artist=match.group("artist").strip() or None,
                version=version,
            )
            if dash_variant not in results:
                results.append(dash_variant)

        # Площадка могла перепутать поля местами (title и artist поменяны местами) —
        # пробуем и такую интерпретацию, раз raw_artist вообще есть.
        if raw_artist and swapped_title is not None:
            swapped_variant = NormalizedTrack(
                title=swapped_title,
                artist=self._clean_artist_field(raw_title) or None,
                version=version,
            )
            if swapped_variant not in results:
                results.append(swapped_variant)

        return results

    def latinize(self, track: NormalizedTrack) -> NormalizedTrack:
        return NormalizedTrack(
            title=transliterate(track.title),
            artist=transliterate(track.artist) if track.artist is not None else None,
            version=track.version,
        )

    def _prepare_title(self, raw_title: str) -> tuple[str, VersionInfo]:
        text = self._fold(raw_title)
        text, version = extract_version(text)
        for pattern in _JUNK_PATTERNS:
            text = pattern.sub(" ", text)
        return _WHITESPACE_PATTERN.sub(" ", text).strip(), version

    def _clean_artist_field(self, text: str) -> str:
        # Лёгкая очистка: без вырезания feat./ft./remaster/prod — "A feat. B" в поле
        # артиста должен дожить до parse_artist_names() и разобраться как {"a", "b"},
        # а не потерять "b" здесь.
        return _WHITESPACE_PATTERN.sub(" ", self._fold(text)).strip()

    def _fold(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        text = text.lower()
        return text.replace("ё", "е")

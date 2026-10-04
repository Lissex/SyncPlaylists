import re
import unicodedata
from dataclasses import dataclass, field
from typing import Final

from syncplaylists.modules.matching.domain.artists import (
    artist_set_similarity,
    parse_artist_names,
)
from syncplaylists.modules.matching.domain.transliteration import transliterate
from syncplaylists.modules.matching.domain.version import VersionInfo, VersionTag, extract_version
from syncplaylists.shared_kernel.domain.base import ValueObject

# Мусор, который реально режется (feat./ft., remaster, Free DL, prod.) — не трогаем
# remix/live/cover и подобные пометки версии: те извлекаются отдельно в VersionInfo
# (см. version.py) ещё до этого шага.
_JUNK_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\(\s*feat\.?[^)]*\)"),
    re.compile(r"\[\s*feat\.?[^\]]*\]"),
    re.compile(r"\(\s*ft\.?\s[^)]*\)"),
    re.compile(r"\[\s*ft\.?\s[^\]]*\]"),
    re.compile(r"\bfeat\.?\s+[^\-([,]+"),
    re.compile(r"\bft\.?\s+[^\-([,]+"),
    re.compile(r"\(\s*remaster(?:ed)?[^)]*\)"),
    re.compile(r"\bremaster(?:ed)?\b"),
    # Теги загрузчиков SoundCloud (по реальным названиям из поиска api-v2, 2026-10-03):
    # «[FREE DL]», «(Free Download)», «*FREE DOWNLOAD*», «BUY = Free Download», «(FREE D/L)».
    re.compile(r"[\(\[]\s*(?:buy\s*=\s*)?free\s*(?:dl|d/l|download)\b[^)\]]*[\)\]]"),
    re.compile(r"(?:\bbuy\s*=\s*)?\*?\bfree\s*(?:dl|d/l|download)\b\*?"),
    re.compile(r"[\(\[\{][^)\]\}]*\bout now\b[^)\]\}]*[\)\]\}]"),
    re.compile(r"(?:\b(?:music\s+vid(?:eo)?|is)\s+)?\bout now\b(?:\s+on\s+[\w .]+)?!*\s*$"),
    # «Premiere:», «PREMIERE060:», «GTG Premiere |», «TC Premiere:» — префикс канала.
    re.compile(r"^[^|:\-–—]{0,30}?\bpremiere\s*\d*\s*(?::|\||/+|[-–—])\s*"),
    re.compile(r"[\(\[]\s*premiere\s*[\)\]]"),
    re.compile(
        r"[\(\[]\s*official\s+(?:audio|video|music\s+video|lyric\s+video|visuali[sz]er)\s*[\)\]]"
    ),
    # Без скобок — «| Official Audio |», «... Official Audio 2018» в конце.
    re.compile(
        r"(?:[|/=]\s*)?\bofficial\s+(?:audio|video|music\s+video|lyric\s+video)\b(?:\s+\d{4})?"
    ),
    re.compile(r"[\(\[]\s*(?:preview|snippet|hq|hd|4k)\s*[\)\]]"),
    re.compile(r"\b\d{3}\s*kbps\b"),
    re.compile(r"(?:^|\s)#\w+"),
    # Каталожный номер лейбла: «[MR047]», «[PHPEP019]».
    re.compile(r"\[\s*[a-z][a-z0-9]{1,15}\s?\d{2,4}\s*\]"),
    # «[prod. X]», «(prod X)», «(ProdByX)», «(@ProdByX)», «[Prod.By X]».
    re.compile(r"[\(\[]\s*@?prod(?:\.?\s|\.?\s*by)[^)\]]*[\)\]]"),
    re.compile(r"\bprod\.?\s+(?:by\s+)?\S+(?:\s+(?:x|&|and)\s+\S+)*"),
    re.compile(r"[\(\[]\s*(?:полная|full)\s+(?:версия|version)\s*[\)\]]"),
)
# Что остаётся после вырезания тегов: пустые скобки и висящие разделители по краям
# («FREE DL | Artist - Title» → «| artist - title»), иначе ломается разбор «Artist - Title».
_EMPTY_BRACKETS: Final = re.compile(r"[\(\[]\s*[\)\]]")
_EDGE_SEPARATORS: Final = re.compile(r"^[\s|*/\-–—]+|[\s|*/\-–—]+$")
_DOUBLE_DASH: Final = re.compile(r"\s[-–—](?:\s+[-–—])+\s")

# «(feat. X)», «[ft. X & Y]», «A feat. B - Title»: X — тоже артист трека. Вырезается из
# названия как мусор, но перед этим добавляется к артистам — иначе «ANIKV, SALUKI» на
# площадке совпадал бы с источником только наполовину (e2e 2026-10-03).
_FEATURED_PATTERN: Final = re.compile(
    r"[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+(?P<bracketed>[^)\]]+)[\)\]]"
    r"|\b(?:feat\.?|ft\.?)\s+(?P<inline>[^\-–—(\[,]+)"
)
_ARTIST_TITLE_PATTERN: Final = re.compile(r"^(?P<artist>.+?)(?:\s[-–—]\s?|[-–—]\s)(?P<title>.+)$")
_WHITESPACE_PATTERN: Final = re.compile(r"\s+")
_SAME_ARTIST: Final = 0.9


@dataclass(frozen=True, slots=True)
class NormalizedTrack(ValueObject):
    title: str
    artist: str | None
    version: VersionInfo = field(default_factory=VersionInfo)
    # Поля поменяны местами (площадка перепутала title/artist). Сравнивать два таких
    # варианта между собой бессмысленно — это просто вес артиста вместо названия.
    swapped: bool = False


class TrackNormalizer:
    def normalize(self, raw_title: str, raw_artist: str | None = None) -> NormalizedTrack:
        cleaned_title, version = self._prepare_title(raw_title)
        featured = self._featured(raw_title)
        match = _ARTIST_TITLE_PATTERN.match(cleaned_title)
        cleaned_artist = self._clean_artist_field(raw_artist) if raw_artist else None
        if match:
            reversed_parse = self._title_then_artist(match, cleaned_artist, featured, version)
            if reversed_parse is not None:
                return reversed_parse
            return NormalizedTrack(
                title=match.group("title").strip(),
                artist=_with_featured(match.group("artist").strip() or None, featured),
                version=version,
            )
        return NormalizedTrack(
            title=cleaned_title,
            artist=_with_featured(cleaned_artist or None, featured),
            version=version,
        )

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

        featured = self._featured(raw_title)
        cleaned_artist = self._clean_artist_field(raw_artist) if raw_artist else None
        fallback = NormalizedTrack(
            title=cleaned_title,
            artist=_with_featured(cleaned_artist or None, featured),
            version=version,
        )
        results = [fallback]

        match = _ARTIST_TITLE_PATTERN.match(cleaned_title)
        if match:
            dash_variant = NormalizedTrack(
                title=match.group("title").strip(),
                artist=_with_featured(match.group("artist").strip() or None, featured),
                version=version,
            )
            if dash_variant not in results:
                results.append(dash_variant)
            reversed_parse = self._title_then_artist(match, cleaned_artist, featured, version)
            if reversed_parse is not None and reversed_parse not in results:
                results.append(reversed_parse)

        # Площадка могла перепутать поля местами (title и artist поменяны местами) —
        # пробуем и такую интерпретацию, раз raw_artist вообще есть.
        if raw_artist and swapped_title is not None:
            swapped_variant = NormalizedTrack(
                title=swapped_title,
                artist=self._clean_artist_field(raw_title) or None,
                version=version,
                swapped=True,
            )
            if swapped_variant not in results:
                results.append(swapped_variant)

        return results

    def latinize(self, track: NormalizedTrack) -> NormalizedTrack:
        return NormalizedTrack(
            title=transliterate(track.title),
            artist=transliterate(track.artist) if track.artist is not None else None,
            version=track.version,
            swapped=track.swapped,
        )

    def _prepare_title(self, raw_title: str) -> tuple[str, VersionInfo]:
        text = self._fold(raw_title)
        text, version = extract_version(text)
        for pattern in _JUNK_PATTERNS:
            text = pattern.sub(" ", text)
        text = _EMPTY_BRACKETS.sub(" ", text)
        text = _DOUBLE_DASH.sub(" - ", _WHITESPACE_PATTERN.sub(" ", text))
        text = _EDGE_SEPARATORS.sub("", text)
        return _WHITESPACE_PATTERN.sub(" ", text).strip(), version

    @staticmethod
    def _title_then_artist(
        match: re.Match[str],
        cleaned_artist: str | None,
        featured: list[str],
        version: VersionInfo,
    ) -> NormalizedTrack | None:
        """«SISTERS & BROTHERS- Kanye West» при артисте «Kanye West, Ye»: справа от тире —
        артист, слева — название. Только когда артист из поля совпадает с правой частью и
        не совпадает с левой, иначе обычный порядок «Artist - Title»."""
        if not cleaned_artist:
            return None
        known = parse_artist_names(cleaned_artist)
        left = parse_artist_names(match.group("artist").strip())
        right = parse_artist_names(match.group("title").strip())
        if artist_set_similarity(right, known) < _SAME_ARTIST or (
            artist_set_similarity(left, known) >= _SAME_ARTIST
        ):
            return None
        return NormalizedTrack(
            title=match.group("artist").strip(),
            artist=_with_featured(cleaned_artist, featured),
            version=version,
        )

    def _featured(self, raw_title: str) -> list[str]:
        names: list[str] = []
        for match in _FEATURED_PATTERN.finditer(self._fold(raw_title)):
            text = match.group("bracketed") or match.group("inline") or ""
            names.extend(sorted(parse_artist_names(text.strip())))
        return names

    def _clean_artist_field(self, text: str) -> str:
        # Лёгкая очистка: без вырезания feat./ft./remaster/prod — "A feat. B" в поле
        # артиста должен дожить до parse_artist_names() и разобраться как {"a", "b"},
        # а не потерять "b" здесь.
        return _WHITESPACE_PATTERN.sub(" ", self._fold(text)).strip()

    def _fold(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        text = text.lower()
        return text.replace("ё", "е")


def _with_featured(artist: str | None, featured: list[str]) -> str | None:
    """Артист + feat-артисты из названия, без повторов. Без основного артиста feat-имена
    не подставляем — иначе приглашённый стал бы «главным»."""
    if artist is None or not featured:
        return artist
    present = parse_artist_names(artist)
    extra = [name for name in featured if name not in present]
    return ", ".join([artist, *extra]) if extra else artist

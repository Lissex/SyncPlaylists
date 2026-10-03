import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from syncplaylists.shared_kernel.domain.base import ValueObject


class VersionTag(StrEnum):
    ORIGINAL = "original"
    LIVE = "live"
    REMIX = "remix"
    ACOUSTIC = "acoustic"
    SPED_UP = "sped_up"
    SLOWED = "slowed"
    COVER = "cover"
    INSTRUMENTAL = "instrumental"
    KARAOKE = "karaoke"
    EXTENDED = "extended"
    RADIO_EDIT = "radio_edit"


@dataclass(frozen=True, slots=True)
class VersionInfo(ValueObject):
    tag: VersionTag = VersionTag.ORIGINAL
    remixer: str | None = None


def _bracket(inner: str) -> re.Pattern[str]:
    # Ключевое слово где угодно внутри скобок — остальной текст внутри не важен,
    # поэтому "(Live Acoustic Version)" целиком ловится как LIVE (по приоритету).
    return re.compile(rf"[\(\[]\s*(?=[^)\]]*\b{inner}\b)[^)\]]*[\)\]]")


def _trailing(inner: str) -> re.Pattern[str]:
    # Без скобок — только в самом конце строки. Намеренно не ловим мусор в середине
    # названия: иначе трек "Live Forever" или "Alive" ложно получит тег LIVE.
    return re.compile(rf"\s+\b{inner}\b\s*$")


# Порядок важен: EXTENDED должен проверяться раньше REMIX, иначе "(Extended Mix)"
# перепутается с ремиксом без имени ремиксера.
_RADIO_EDIT_PATTERNS = (_bracket(r"radio\s*edit"), _trailing(r"radio\s*edit"))
_EXTENDED_PATTERNS = (
    _bracket(r"extended(?:\s*(?:mix|version|edit))?"),
    _trailing(r"extended(?:\s*(?:mix|version|edit))?"),
)
_REMIX_NAMED_BRACKET = re.compile(r"[\(\[]\s*(?P<remixer>[\w .&'-]+?)\s+remix\b[^)\]]*[\)\]]")
_REMIX_BY_BRACKET = re.compile(r"[\(\[]\s*remix(?:ed)?\s+by\s+(?P<remixer>[\w .&'-]+?)\s*[\)\]]")
_REMIX_BY_TRAILING = re.compile(r"\s+remix(?:ed)?\s+by\s+(?P<remixer>[\w .&'-]+)\s*$")
_REMIX_BARE_BRACKET = _bracket("remix")
_REMIX_BARE_TRAILING = _trailing(r"remix(?:ed)?")

# DJ-версии с SoundCloud: «(Ed Marquis Bootleg)», «[No Romeo Schranz Edit]», «(X Rmx)»,
# «(X Flip)», «Song - KAAI Edit». Для матчинга это тот же ремикс: нельзя молча взять
# вместо него оригинал. Проверяются после radio/extended edit, чтобы «(Radio Edit)» не
# стал ремиксом «radio».
_DJ_VERSION_WORDS: Final = r"(?:bootleg(?:\s+edit)?|edit|rmx|flip|rework|vip(?:\s+mix)?|mashup)"
_DJ_NAMED_BRACKET = re.compile(
    rf"[\(\[]\s*(?P<remixer>[\w .&'’-]+?)\s+{_DJ_VERSION_WORDS}\b[^)\]]*[\)\]]"
)
# Только через дефис в конце: «Song - Name Edit». Без «remix» — «Drake - God's Plan
# Remix» иначе стал бы ремиксом «god's plan».
_DJ_NAMED_TRAILING = re.compile(rf"\s+[-–—]\s+(?P<remixer>[\w .&'’-]+?)\s+{_DJ_VERSION_WORDS}\s*$")
_DJ_BARE_PATTERNS = (
    _bracket(r"(?:bootleg|rmx|flip|rework|vip(?:\s+mix)?|mashup)"),
    _trailing(r"(?:bootleg|rmx|vip)"),
)
# Служебные правки площадок — не DJ-версии: «(Clean Edit)», «(Explicit Edit)».
_SERVICE_EDIT_WORDS: Final = frozenset(
    {
        "radio",
        "extended",
        "clean",
        "explicit",
        "dirty",
        "censored",
        "short",
        "single",
        "album",
        "original",
        "tv",
        "main",
        "uncensored",
        "edit",
    }
)
# Жанр перед словом версии — не часть имени ремиксера: «(Bonkers Hardstyle Remix)».
_GENRE_WORDS: Final = frozenset(
    {
        "hardstyle",
        "uptempo",
        "schranz",
        "techno",
        "frenchcore",
        "hardtekk",
        "hard",
        "indus",
        "industrial",
        "gabber",
        "melodic",
        "rolling",
        "house",
        "bassline",
        "moombahton",
        "jumpstyle",
        "breakbeat",
        "drill",
        "phonk",
        "trap",
        "dnb",
        "d&b",
        "dubstep",
        "club",
        "festival",
        "bootleg",
        "makina",
        "hardcore",
        "trance",
        "garage",
    }
)


def _clean_remixer(name: str) -> str:
    words = name.split()
    while len(words) > 1 and words[-1] in _GENRE_WORDS:
        words.pop()
    return " ".join(words).strip(" -")


def _remove_span(text: str, start: int, end: int) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text[:start] + text[end:]).strip()


def _dj_version(text: str) -> tuple[str, VersionInfo] | None:
    for pattern in (_DJ_NAMED_BRACKET, _DJ_NAMED_TRAILING):
        match = pattern.search(text)
        if match is None:
            continue
        remixer = _clean_remixer(match.group("remixer").strip())
        if remixer in _SERVICE_EDIT_WORDS or not remixer:
            continue
        return _remove_span(text, match.start(), match.end()), VersionInfo(
            VersionTag.REMIX, remixer
        )
    remaining, found = _try_remove(text, _DJ_BARE_PATTERNS)
    if found:
        return _WHITESPACE_PATTERN.sub(" ", remaining).strip(), VersionInfo(VersionTag.REMIX)
    return None


_LIVE_PATTERNS = (_bracket("live"), _trailing("live"))
_ACOUSTIC_PATTERNS = (_bracket(r"acoustic(?:\s+version)?"), _trailing(r"acoustic(?:\s+version)?"))
_SPED_UP_PATTERNS = (_bracket(r"sped[\s-]?up"), _trailing(r"sped[\s-]?up"))
_SLOWED_PATTERNS = (
    _bracket(r"slowed(?:\s+down)?(?:\s*\+?\s*reverb)?"),
    _trailing(r"slowed(?:\s+down)?(?:\s*\+?\s*reverb)?"),
)
_COVER_PATTERNS = (_bracket(r"cover(?:\s+by\s+[\w .&'-]+)?"), _trailing("cover"))
_INSTRUMENTAL_PATTERNS = (_bracket("instrumental"), _trailing("instrumental"))
_KARAOKE_PATTERNS = (_bracket("karaoke"), _trailing("karaoke"))

_WHITESPACE_PATTERN: Final = re.compile(r"\s+")


def _try_remove(text: str, patterns: tuple[re.Pattern[str], ...]) -> tuple[str, bool]:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return text[: match.start()] + text[match.end() :], True
    return text, False


def extract_version(text: str) -> tuple[str, VersionInfo]:
    for remix_pattern in (_REMIX_NAMED_BRACKET, _REMIX_BY_BRACKET, _REMIX_BY_TRAILING):
        match = remix_pattern.search(text)
        if match:
            remaining = text[: match.start()] + text[match.end() :]
            remaining = _WHITESPACE_PATTERN.sub(" ", remaining).strip()
            remixer = _clean_remixer(match.group("remixer").strip())
            return remaining, VersionInfo(VersionTag.REMIX, remixer)

    for tag, patterns in (
        (VersionTag.RADIO_EDIT, _RADIO_EDIT_PATTERNS),
        (VersionTag.EXTENDED, _EXTENDED_PATTERNS),
    ):
        remaining, found = _try_remove(text, patterns)
        if found:
            return _WHITESPACE_PATTERN.sub(" ", remaining).strip(), VersionInfo(tag)

    dj_version = _dj_version(text)
    if dj_version is not None:
        return dj_version

    checks: tuple[tuple[VersionTag, tuple[re.Pattern[str], ...]], ...] = (
        (VersionTag.REMIX, (_REMIX_BARE_BRACKET, _REMIX_BARE_TRAILING)),
        (VersionTag.LIVE, _LIVE_PATTERNS),
        (VersionTag.ACOUSTIC, _ACOUSTIC_PATTERNS),
        (VersionTag.SPED_UP, _SPED_UP_PATTERNS),
        (VersionTag.SLOWED, _SLOWED_PATTERNS),
        (VersionTag.COVER, _COVER_PATTERNS),
        (VersionTag.INSTRUMENTAL, _INSTRUMENTAL_PATTERNS),
        (VersionTag.KARAOKE, _KARAOKE_PATTERNS),
    )
    for tag, patterns in checks:
        remaining, found = _try_remove(text, patterns)
        if found:
            return _WHITESPACE_PATTERN.sub(" ", remaining).strip(), VersionInfo(tag)

    return text, VersionInfo()


def versions_match(a: VersionInfo, b: VersionInfo) -> bool:
    """Одна и та же версия записи. Ремикс без имени ремиксера с одной из сторон не
    считается несовпадением — площадки часто пишут просто "(Remix)"."""
    if a.tag != b.tag:
        return False
    return not (
        a.tag is VersionTag.REMIX and bool(a.remixer) and bool(b.remixer) and a.remixer != b.remixer
    )


_SEARCH_SUFFIXES: Final[dict[VersionTag, str]] = {
    VersionTag.LIVE: "live",
    VersionTag.ACOUSTIC: "acoustic",
    VersionTag.SPED_UP: "sped up",
    VersionTag.SLOWED: "slowed",
    VersionTag.INSTRUMENTAL: "instrumental",
    VersionTag.EXTENDED: "extended mix",
    VersionTag.RADIO_EDIT: "radio edit",
}


def version_search_suffix(version: VersionInfo) -> str | None:
    """Что дописать к поисковому запросу, чтобы найти на площадке ту же версию.
    None — для оригинала и версий, которые поиском по слову не находятся (cover,
    karaoke: там важнее исполнитель, а не пометка)."""
    if version.tag is VersionTag.REMIX:
        return f"{version.remixer} remix" if version.remixer else "remix"
    return _SEARCH_SUFFIXES.get(version.tag)

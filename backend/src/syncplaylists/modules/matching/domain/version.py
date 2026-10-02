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
            return remaining, VersionInfo(VersionTag.REMIX, match.group("remixer").strip())

    checks: tuple[tuple[VersionTag, tuple[re.Pattern[str], ...]], ...] = (
        (VersionTag.RADIO_EDIT, _RADIO_EDIT_PATTERNS),
        (VersionTag.EXTENDED, _EXTENDED_PATTERNS),
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

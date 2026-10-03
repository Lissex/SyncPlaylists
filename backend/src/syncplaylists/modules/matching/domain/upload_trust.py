"""Официальная заливка или перезалив — для площадок с пользовательскими заливками
(SoundCloud). Один и тот же трек там часто лежит и у артиста/лейбла, и у фан-страниц
(«trap.tunes.daily: Juice WRLD - Lucid Dreams»): текст и длительность совпадают, и
скорер их не различает. Различает заливщик."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final

from rapidfuzz import fuzz

from syncplaylists.modules.matching.domain.transliteration import transliterate
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import MatchScore

_NON_ALNUM: Final = re.compile(r"[\W_]+")
# Короче — слишком легко найти внутри любого ника («mc» в «mcdonalds_music»).
_MIN_CONTAINED_LENGTH: Final = 4


class UploadKind(StrEnum):
    OFFICIAL = "official"  # правообладатель или заливщик ≈ артист
    REUPLOAD = "reupload"  # заливщик — не артист
    UNKNOWN = "unknown"  # площадка без заливщиков (Spotify, Яндекс, ...)


@dataclass(frozen=True, slots=True)
class UploadVerdict:
    kind: UploadKind
    # Артист источника, с которым совпал ник заливщика: заливщик и есть артист, просто
    # записан иначе («JuiceWRLDofficial») — скорер сравнивает артиста по нему.
    uploader_artist: str | None = None


def _compact(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    return _NON_ALNUM.sub("", transliterate(folded))


class UploadTrust:
    OFFICIAL_BONUS: ClassVar[float] = 0.03
    REUPLOAD_PENALTY: ClassVar[float] = 0.05
    # Кандидаты ближе этого к лучшему считаются равными — тогда побеждает официальный.
    TIE_MARGIN: ClassVar[float] = 0.02
    _UPLOADER_SIMILARITY: ClassVar[float] = 85.0

    def classify(self, candidate: TrackCandidate, source_artists: Iterable[str]) -> UploadVerdict:
        if candidate.uploader is None:
            return UploadVerdict(UploadKind.UNKNOWN)
        uploader = _compact(candidate.uploader)
        for artist in source_artists:
            name = _compact(artist)
            if not name or not uploader:
                continue
            # «lilpeepofficial», «kinoband» — ник артиста с хвостом.
            contained = len(name) >= _MIN_CONTAINED_LENGTH and name in uploader
            if contained or fuzz.ratio(uploader, name) >= self._UPLOADER_SIMILARITY:
                return UploadVerdict(UploadKind.OFFICIAL, uploader_artist=artist)
        if candidate.rights_holder:
            return UploadVerdict(UploadKind.OFFICIAL)
        return UploadVerdict(UploadKind.REUPLOAD)

    def adjust(self, score: MatchScore, kind: UploadKind) -> MatchScore:
        if kind is UploadKind.OFFICIAL:
            value = score.value + self.OFFICIAL_BONUS
        elif kind is UploadKind.REUPLOAD:
            value = score.value - self.REUPLOAD_PENALTY
        else:
            return score
        return MatchScore(min(1.0, max(0.0, value)))

    def pick_best[T](self, scored: list[tuple[MatchScore, UploadKind, T]]) -> tuple[MatchScore, T]:
        """Лучший по баллу; при близких баллах (TIE_MARGIN) — официальная заливка."""
        best_score = max(score.value for score, _, _ in scored)
        close = [entry for entry in scored if best_score - entry[0].value <= self.TIE_MARGIN]
        official = [entry for entry in close if entry[1] is UploadKind.OFFICIAL]
        score, _, item = max(official or close, key=lambda entry: entry[0].value)
        return score, item

"""Поиск на целевой площадке по тому, что распознаватель услышал в треке источника
(этап 6, ARCHITECTURE.md 11j).

Найдено по звуку (AUTO, method=audio) — только при всех условиях сразу:
- распознана та же запись без изменения скорости и тона (RecognizedTrack.exact);
- распознанное хоть немного похоже на название источника — иначе это вероятнее ошибка
  распознавателя (slowed + reverb Shazam уверенно принял за посторонний трек);
- совпадают версия и длительность: их скорер берёт от источника, а не от распознанного,
  — ремикс Shazam узнаёт как оригинал, а нам нужен ремикс.
Иначе распознанное — подсказка: кандидаты не выше UNCERTAIN, причина — в AudioVerdict.
"""

import dataclasses
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import uuid4

from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.modules.matching.domain.normalization import NormalizedTrack, TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchAttempt, MatchStatus
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.version import VersionInfo, version_search_suffix
from syncplaylists.shared_kernel.domain.base import ValueObject
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.search import (
    RecognizedTrack,
    TrackCandidate,
    TrackQuery,
)
from syncplaylists.shared_kernel.domain.value_objects import MatchScore, MatchTier, Platform

# Ниже — распознанное не похоже на источник ни названием, ни артистом.
_AGREEMENT_GATE: Final = 0.5
_DURATION_TOLERANCE_MS: Final = 3000
# Название источника, по которому сверять нечего: «track 01», «audio_2024», «запись 3».
_UNINTELLIGIBLE: Final = re.compile(
    r"^(?:track|audio|untitled|unknown|record(?:ing)?|rec|voice|new|"
    r"трек|запись|аудио|новая запись|без названия)?[\s_\-#.]*\d*$"
)
_LETTER: Final = re.compile(r"[^\W\d_]")


class AudioVerdict(StrEnum):
    EXACT = "exact"  # найдено по звуку
    ALTERED = "altered"  # звук изменён или распознаватель не уверен — подсказка
    TEXT_MISMATCH = "text_mismatch"  # распознанное противоречит названию источника
    NOT_ON_TARGET = "not_on_target"  # распознано, но на цели уверенно не нашлось


@dataclass(frozen=True, slots=True)
class AudioMatchOutcome(ValueObject):
    attempt: MatchAttempt
    verdict: AudioVerdict


class AudioMatcher:
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

    async def match(
        self, source: TrackCandidate, heard: RecognizedTrack, target_platform: Platform
    ) -> AudioMatchOutcome:
        source_variants = self._normalizer.variants(source.title, source.artist)
        source_version = source_variants[0].version
        # Распознанное — с версией источника: скорер сравнит версию кандидата с ней.
        heard_variants = [
            dataclasses.replace(variant, version=source_version)
            for variant in self._normalizer.variants(heard.title, heard.artist)
        ]
        trusted_text = self._text_agrees(source_variants, heard_variants)

        candidates = await self._candidates(source, heard, heard_variants[0], source_version)
        if not candidates:
            verdict = self._doubt(heard, trusted_text) or AudioVerdict.NOT_ON_TARGET
            return AudioMatchOutcome(
                MatchAttempt(status=MatchStatus.NOT_FOUND, method=MatchMethod.AUDIO), verdict
            )

        scored = [(self._score(source, heard, heard_variants, c), c) for c in candidates]
        best_score, best = max(scored, key=lambda pair: pair[0].value)
        doubt = self._doubt(heard, trusted_text)
        if doubt is not None:
            best_score = self._scorer.cap_below_auto(best_score)
        all_candidates = tuple(candidate for _, candidate in scored)

        if best_score.tier is MatchTier.AUTO:
            match = TrackMatch(
                id=uuid4(),
                source_ref=source.ref,
                target_platform=target_platform,
                target_ref=best.ref,
                method=MatchMethod.AUDIO,
                score=best_score,
                restriction=best.restriction,
            )
            attempt = MatchAttempt(
                status=MatchStatus.MATCHED,
                match=match,
                candidates=all_candidates,
                method=MatchMethod.AUDIO,
            )
            return AudioMatchOutcome(attempt, AudioVerdict.EXACT)
        status = (
            MatchStatus.UNCERTAIN
            if best_score.tier is MatchTier.UNCERTAIN
            else MatchStatus.NOT_FOUND
        )
        attempt = MatchAttempt(status=status, candidates=all_candidates, method=MatchMethod.AUDIO)
        return AudioMatchOutcome(attempt, doubt or AudioVerdict.NOT_ON_TARGET)

    async def _candidates(
        self,
        source: TrackCandidate,
        heard: RecognizedTrack,
        heard_parsed: NormalizedTrack,
        source_version: VersionInfo,
    ) -> list[TrackCandidate]:
        # ISRC в приоритете: точнее любого текста. Площадка без поиска по ISRC (SoundCloud)
        # или пустой ответ — поиск по распознанным артисту и названию.
        if heard.isrc is not None:
            found = await self._gateway.search_by_isrc(heard.isrc)
            if found:
                return found
        title = heard_parsed.title or heard.title
        suffix = version_search_suffix(source_version)
        if suffix:
            title = f"{title} {suffix}"
        query = TrackQuery(title=title, artist=heard_parsed.artist, duration=source.duration)
        return await self._gateway.search(query, limit=self._search_limit)

    def _score(
        self,
        source: TrackCandidate,
        heard: RecognizedTrack,
        heard_variants: list[NormalizedTrack],
        candidate: TrackCandidate,
    ) -> MatchScore:
        candidate_variants = self._normalizer.variants(candidate.title, candidate.artist)
        if heard.isrc is not None and candidate.isrc == heard.isrc:
            score = MatchScore(1.0)
            if (
                source.duration is not None
                and candidate.duration is not None
                and not source.duration.is_close_to(candidate.duration, _DURATION_TOLERANCE_MS)
            ):
                score = self._scorer.cap_below_auto(score)
            return self._scorer.cap_version_mismatch(
                score, heard_variants[0].version, candidate_variants[0].version
            )
        # Длительность — источника: распознанная запись могла быть другой длины (edit).
        return self._scorer.best_score(
            heard_variants, source.duration, candidate_variants, candidate.duration
        )

    def _text_agrees(
        self, source_variants: list[NormalizedTrack], heard_variants: list[NormalizedTrack]
    ) -> bool:
        if _unintelligible(source_variants[0].title):
            return True  # сверять не с чем — верим звуку
        agreement = max(
            self._scorer.agreement(s, h)
            for s in source_variants
            for h in heard_variants
            if not (s.swapped and h.swapped)
        )
        return agreement >= _AGREEMENT_GATE

    @staticmethod
    def _doubt(heard: RecognizedTrack, trusted_text: bool) -> AudioVerdict | None:
        if not heard.exact:
            return AudioVerdict.ALTERED
        if not trusted_text:
            return AudioVerdict.TEXT_MISMATCH
        return None


def _unintelligible(title: str) -> bool:
    text = title.strip().lower()
    return len(_LETTER.findall(text)) < 2 or _UNINTELLIGIBLE.match(text) is not None

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.shared_kernel.domain.value_objects import Duration

_DEFAULT_FIXTURES_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "dirty_titles.json"


@dataclass(frozen=True, slots=True)
class CorpusEntryResult:
    id: str
    category: str
    expected_tier: str
    computed_tier: str
    score: float

    @property
    def is_correct(self) -> bool:
        return self.computed_tier == self.expected_tier

    @property
    def is_false_auto(self) -> bool:
        return self.computed_tier == "auto" and self.expected_tier != "auto"


@dataclass(frozen=True, slots=True)
class CategoryAccuracy:
    category: str
    total: int
    correct: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0


@dataclass(frozen=True, slots=True)
class CorpusReport:
    entries: tuple[CorpusEntryResult, ...]

    @property
    def overall_accuracy(self) -> float:
        if not self.entries:
            return 1.0
        correct = sum(1 for entry in self.entries if entry.is_correct)
        return correct / len(self.entries)

    @property
    def false_auto_entries(self) -> tuple[CorpusEntryResult, ...]:
        return tuple(entry for entry in self.entries if entry.is_false_auto)

    @property
    def failing_entries(self) -> tuple[CorpusEntryResult, ...]:
        return tuple(entry for entry in self.entries if not entry.is_correct)

    @property
    def by_category(self) -> tuple[CategoryAccuracy, ...]:
        categories: dict[str, list[CorpusEntryResult]] = {}
        for entry in self.entries:
            categories.setdefault(entry.category, []).append(entry)
        return tuple(
            CategoryAccuracy(
                category=category,
                total=len(entries),
                correct=sum(1 for e in entries if e.is_correct),
            )
            for category, entries in sorted(categories.items())
        )


def load_corpus(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or _DEFAULT_FIXTURES_PATH
    data: list[dict[str, Any]] = json.loads(target.read_text(encoding="utf-8"))
    return data


def evaluate_corpus(
    corpus: list[dict[str, Any]], normalizer: TrackNormalizer, scorer: MatchScorer
) -> CorpusReport:
    results = []
    for entry in corpus:
        source_variants = normalizer.variants(entry["raw_title"], entry["raw_artist"])
        candidate_variants = normalizer.variants(entry["expected_title"], entry["expected_artist"])
        source_duration = (
            Duration(entry["raw_duration_ms"]) if entry.get("raw_duration_ms") is not None else None
        )
        candidate_duration = (
            Duration(entry["expected_duration_ms"])
            if entry.get("expected_duration_ms") is not None
            else None
        )
        score = scorer.best_score(
            source_variants, source_duration, candidate_variants, candidate_duration
        )
        results.append(
            CorpusEntryResult(
                id=entry["id"],
                category=entry.get("category", "uncategorized"),
                expected_tier=entry["expected_tier"],
                computed_tier=score.tier.value,
                score=score.value,
            )
        )
    return CorpusReport(entries=tuple(results))

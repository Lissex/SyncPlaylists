from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from tests.unit.matching.corpus_report import evaluate_corpus, load_corpus

_ACCURACY_THRESHOLD = 0.85


def test_corpus_accuracy_meets_threshold() -> None:
    report = evaluate_corpus(load_corpus(), TrackNormalizer(), MatchScorer(TrackNormalizer()))

    if report.overall_accuracy < _ACCURACY_THRESHOLD:
        lines = [
            f"{e.id} [{e.category}]: ожидали {e.expected_tier}, получили {e.computed_tier} "
            f"(score={e.score:.3f})"
            for e in report.failing_entries
        ]
        by_category = "\n".join(
            f"  {c.category}: {c.correct}/{c.total} ({c.accuracy:.0%})" for c in report.by_category
        )
        raise AssertionError(
            f"точность {report.overall_accuracy:.2%} ниже порога {_ACCURACY_THRESHOLD:.0%}:\n"
            + "\n".join(lines)
            + "\n\nпо категориям:\n"
            + by_category
        )


def test_corpus_has_zero_false_auto() -> None:
    report = evaluate_corpus(load_corpus(), TrackNormalizer(), MatchScorer(TrackNormalizer()))

    if report.false_auto_entries:
        lines = [
            f"{e.id} [{e.category}]: ожидали {e.expected_tier}, но сервис выбрал AUTO "
            f"(score={e.score:.3f})"
            for e in report.false_auto_entries
        ]
        raise AssertionError("false AUTO недопустим:\n" + "\n".join(lines))

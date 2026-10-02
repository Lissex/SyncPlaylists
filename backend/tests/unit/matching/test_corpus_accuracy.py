import json
from pathlib import Path
from typing import Any

from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.shared_kernel.domain.value_objects import Duration

_FIXTURES_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "dirty_titles.json"
_ACCURACY_THRESHOLD = 0.85


def _load_corpus() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(_FIXTURES_PATH.read_text(encoding="utf-8"))
    return data


def test_corpus_accuracy_meets_threshold() -> None:
    normalizer = TrackNormalizer()
    scorer = MatchScorer(normalizer)
    corpus = _load_corpus()

    failures: list[str] = []
    for entry in corpus:
        source = normalizer.normalize(entry["raw_title"], entry["raw_artist"])
        expected = normalizer.normalize(entry["expected_title"], entry["expected_artist"])
        score = scorer.score(
            source,
            Duration(entry["raw_duration_ms"]),
            expected,
            Duration(entry["expected_duration_ms"]),
        )
        if score.tier.value != entry["expected_tier"]:
            failures.append(
                f"{entry['id']}: ожидали {entry['expected_tier']}, "
                f"получили {score.tier.value} (score={score.value:.3f})"
            )

    accuracy = (len(corpus) - len(failures)) / len(corpus)
    assert accuracy >= _ACCURACY_THRESHOLD, (
        f"точность {accuracy:.2%} ниже порога {_ACCURACY_THRESHOLD:.0%}:\n" + "\n".join(failures)
    )

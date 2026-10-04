"""Промахи e2e «лайки SoundCloud → Яндекс» (2026-10-04, 64/88): коллаборации, отсечка
по названию, «Title- Artist», мусор «(полная версия)» и «prod. A x B»."""

from syncplaylists.modules.matching.domain.artists import (
    artist_set_similarity,
    parse_artist_names,
)
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.shared_kernel.domain.value_objects import Duration, MatchScore


def _best(source: tuple[str, str, int], candidate: tuple[str, str, int]) -> float:
    normalizer = TrackNormalizer()
    scorer = MatchScorer(normalizer)
    return scorer.best_score(
        normalizer.variants(source[0], source[1]),
        Duration(source[2]),
        normalizer.variants(candidate[0], candidate[1]),
        Duration(candidate[2]),
    ).value


def test_all_source_artists_inside_collab_is_high() -> None:
    # «Монетка» — ЛСП против «СД, ЛСП, BOOKER, Вири Альди — Монетка».
    similarity = artist_set_similarity(
        parse_artist_names("лсп"), parse_artist_names("сд, лсп, booker, вири альди")
    )
    assert similarity >= 0.9
    # Не наоборот — чужой артист внутри нашей коллаборации ничего не доказывает.
    assert artist_set_similarity(parse_artist_names("a, b, c, d"), parse_artist_names("x")) == 0


def test_collab_with_same_title_beats_same_artist_other_song() -> None:
    source = ("Монетка", "ЛСП", 185_000)
    collab = _best(source, ("Монетка", "СД, ЛСП, BOOKER, Вири Альди", 185_000))
    other_song = _best(source, ("Холостяк", "ЛСП", 181_000))
    assert collab >= MatchScore.AUTO_THRESHOLD
    assert collab > other_song


def test_other_title_of_same_artist_is_not_even_uncertain() -> None:
    # Раньше 0.76 / 0.75 / 0.77 — шум на ручной проверке.
    assert _best(("Монетка", "ЛСП", 185_000), ("Холостяк", "ЛСП", 181_000)) < (
        MatchScore.UNCERTAIN_THRESHOLD
    )
    assert _best(("Лоботомия", "FENDIGLOCK", 100_000), ("Ненормальный", "FENDIGLOCK", 98_000)) < (
        MatchScore.UNCERTAIN_THRESHOLD
    )
    assert _best(("не тебе", "LILDRUGHILL", 131_000), ("Но уже", "LILDRUGHILL", 125_000)) < (
        MatchScore.UNCERTAIN_THRESHOLD
    )


def test_title_dash_artist_without_left_space() -> None:
    # «SISTERS & BROTHERS- Kanye West», артист в поле — Kanye West: справа артист.
    result = TrackNormalizer().normalize("SISTERS & BROTHERS- Kanye West", "Kanye West, Ye")
    assert (result.artist, result.title) == ("kanye west, ye", "sisters & brothers")
    score = _best(
        ("SISTERS & BROTHERS- Kanye West", "Kanye West, Ye", 165_000),
        ("Sisters & Brothers", "Kanye West", 165_000),
    )
    assert score >= MatchScore.AUTO_THRESHOLD


def test_hyphenated_names_are_not_split() -> None:
    result = TrackNormalizer().normalize("Jay-Z - Empire State of Mind")
    assert (result.artist, result.title) == ("jay-z", "empire state of mind")


def test_full_version_tag_and_prod_chain_are_junk() -> None:
    result = TrackNormalizer().normalize(
        "FENDIGLOCK - INTERVIEW prod. FERDI x THEKGTRAX (полная версия)", "fendiglock"
    )
    assert (result.artist, result.title) == ("fendiglock", "interview")

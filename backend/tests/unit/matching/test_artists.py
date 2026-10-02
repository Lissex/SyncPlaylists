import pytest

from syncplaylists.modules.matching.domain.artists import artist_set_similarity, parse_artist_names


@pytest.mark.parametrize(
    "text",
    ["a, b", "b & a", "a feat. b", "a ft. b", "a x b", "a and b", "b, a"],
)
def test_parse_artist_names_equivalent_separators(text: str) -> None:
    assert parse_artist_names(text) == frozenset({"a", "b"})


def test_parse_artist_names_does_not_split_featuring() -> None:
    assert parse_artist_names("the weeknd featuring daft punk") == frozenset(
        {"the weeknd featuring daft punk"}
    )


def test_parse_artist_names_single_artist() -> None:
    assert parse_artist_names("the weeknd") == frozenset({"the weeknd"})


def test_parse_artist_names_empty() -> None:
    assert parse_artist_names(None) == frozenset()
    assert parse_artist_names("") == frozenset()


def test_artist_set_similarity_same_set_different_order_is_perfect() -> None:
    a = parse_artist_names("post malone, swae lee")
    b = parse_artist_names("swae lee & post malone")
    assert artist_set_similarity(a, b) == 1.0


def test_artist_set_similarity_partial_overlap_is_penalized() -> None:
    a = frozenset({"a", "b"})
    b = frozenset({"a"})
    assert artist_set_similarity(a, b) == pytest.approx(0.5)


def test_artist_set_similarity_disjoint_is_low() -> None:
    a = frozenset({"a"})
    b = frozenset({"completely different"})
    assert artist_set_similarity(a, b) < 0.3


def test_artist_set_similarity_both_empty_is_perfect() -> None:
    assert artist_set_similarity(frozenset(), frozenset()) == 1.0


def test_artist_set_similarity_one_empty_is_zero() -> None:
    assert artist_set_similarity(frozenset({"a"}), frozenset()) == 0.0

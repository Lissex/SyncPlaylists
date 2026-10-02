import pytest

from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    MatchScore,
    MatchTier,
    Platform,
)


def test_isrc_accepts_valid_code() -> None:
    assert ISRC("USRC17607839").value == "USRC17607839"


@pytest.mark.parametrize(
    "value",
    ["usrc17607839", "US-RC17607839", "USRC1760783", "USRC176078399", ""],
)
def test_isrc_rejects_invalid_code(value: str) -> None:
    with pytest.raises(ValueError, match="Некорректный ISRC"):
        ISRC(value)


def test_duration_rejects_negative() -> None:
    with pytest.raises(ValueError, match="отрицательной"):
        Duration(-1)


@pytest.mark.parametrize(
    ("a_ms", "b_ms", "tolerance_ms", "expected"),
    [
        (200_000, 202_000, 3000, True),
        (200_000, 203_000, 3000, True),
        (200_000, 203_001, 3000, False),
        (200_000, 150_000, 3000, False),
    ],
)
def test_duration_is_close_to(a_ms: int, b_ms: int, tolerance_ms: int, expected: bool) -> None:
    assert Duration(a_ms).is_close_to(Duration(b_ms), tolerance_ms=tolerance_ms) is expected


def test_external_track_ref_equality_by_value() -> None:
    assert ExternalTrackRef(Platform.SPOTIFY, "123") == ExternalTrackRef(Platform.SPOTIFY, "123")
    assert ExternalTrackRef(Platform.SPOTIFY, "123") != ExternalTrackRef(Platform.VK, "123")


def test_match_score_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        MatchScore(1.1)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        MatchScore(-0.1)


@pytest.mark.parametrize(
    ("value", "expected_tier"),
    [
        (1.0, MatchTier.AUTO),
        (0.90, MatchTier.AUTO),
        (0.8999, MatchTier.UNCERTAIN),
        (0.70, MatchTier.UNCERTAIN),
        (0.6999, MatchTier.NOT_FOUND),
        (0.0, MatchTier.NOT_FOUND),
    ],
)
def test_match_score_tier_boundaries(value: float, expected_tier: MatchTier) -> None:
    assert MatchScore(value).tier is expected_tier

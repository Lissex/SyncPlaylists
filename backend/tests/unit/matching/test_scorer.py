from syncplaylists.modules.matching.domain.normalization import NormalizedTrack, TrackNormalizer
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.shared_kernel.domain.value_objects import Duration, MatchTier


def _scorer() -> MatchScorer:
    return MatchScorer(TrackNormalizer())


def test_exact_match_scores_auto() -> None:
    track = NormalizedTrack(title="starboy", artist="the weeknd")
    score = _scorer().score(track, Duration(230_000), track, Duration(230_000))
    assert score.tier is MatchTier.AUTO
    assert score.value == 1.0


def test_unrelated_tracks_score_not_found() -> None:
    source = NormalizedTrack(title="radioactive", artist="imagine dragons")
    candidate = NormalizedTrack(title="demons", artist="imagine dragons")
    score = _scorer().score(source, Duration(187_000), candidate, Duration(247_000))
    assert score.tier is MatchTier.NOT_FOUND


def test_large_duration_gap_pulls_a_strong_text_match_down_to_uncertain() -> None:
    source = NormalizedTrack(
        title="don't you worry child (live at tomorrowland 2023)",
        artist="swedish house mafia",
    )
    candidate = NormalizedTrack(title="don't you worry child", artist="swedish house mafia")
    score = _scorer().score(source, Duration(372_000), candidate, Duration(203_000))
    assert score.tier is MatchTier.UNCERTAIN


def test_missing_duration_does_not_crash_and_is_treated_as_neutral() -> None:
    track = NormalizedTrack(title="starboy", artist="the weeknd")
    score = _scorer().score(track, None, track, None)
    assert score.tier is MatchTier.AUTO


def test_title_moves_score_more_than_artist() -> None:
    scorer = _scorer()
    base = NormalizedTrack(title="starboy", artist="the weeknd")
    wrong_title = NormalizedTrack(title="completely different song", artist="the weeknd")
    wrong_artist = NormalizedTrack(title="starboy", artist="completely different artist")
    score_wrong_title = scorer.score(base, Duration(230_000), wrong_title, Duration(230_000))
    score_wrong_artist = scorer.score(base, Duration(230_000), wrong_artist, Duration(230_000))
    assert score_wrong_title.value < score_wrong_artist.value


def test_artist_moves_score_more_than_duration() -> None:
    scorer = _scorer()
    base = NormalizedTrack(title="starboy", artist="the weeknd")
    wrong_artist = NormalizedTrack(title="starboy", artist="completely different artist")
    score_wrong_artist = scorer.score(base, Duration(230_000), wrong_artist, Duration(230_000))
    score_wrong_duration = scorer.score(base, Duration(230_000), base, Duration(400_000))
    assert score_wrong_duration.value > score_wrong_artist.value

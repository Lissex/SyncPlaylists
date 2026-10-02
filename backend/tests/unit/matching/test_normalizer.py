from syncplaylists.modules.matching.domain.normalization import NormalizedTrack, TrackNormalizer


def test_normalize_lowercases_and_folds_yo() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize("Ёжик В Тумане", "Алиса")
    assert result.title == "ежик в тумане"
    assert result.artist == "алиса"


def test_normalize_parses_artist_title_from_title_over_raw_artist() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize("Макс Корж - Малый напросился", "Музыка | Хиты 2024")
    assert result.artist == "макс корж"
    assert result.title == "малый напросился"


def test_normalize_falls_back_to_raw_artist_when_no_dash_in_title() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize("Believer", "Imagine Dragons")
    assert result.artist == "imagine dragons"
    assert result.title == "believer"


def test_normalize_strips_feat_and_remaster() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize("The Weeknd ft. Daft Punk - Starboy (Remastered 2022)")
    assert result.artist == "the weeknd"
    assert result.title == "starboy"


def test_normalize_strips_bracketed_prod() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize("Lil Peep - Falling Down [prod. by Smokeasac]")
    assert result.artist == "lil peep"
    assert result.title == "falling down"


def test_normalize_does_not_strip_version_markers() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize(
        "Swedish House Mafia - Don't You Worry Child (Live at Tomorrowland 2023)"
    )
    assert "live" in result.title


def test_normalize_hyphen_inside_artist_name_does_not_break_split() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize("Jay-Z feat. Alicia Keys - Empire State of Mind (Remaster)")
    assert result.artist == "jay-z"
    assert result.title == "empire state of mind"


def test_normalize_is_idempotent() -> None:
    normalizer = TrackNormalizer()
    once = normalizer.normalize("Billie Eilish - bad guy (Lyrics) HQ Audio")
    twice = normalizer.normalize(once.title, once.artist)
    assert once == twice


def test_latinize_transliterates_both_fields() -> None:
    normalizer = TrackNormalizer()
    track = NormalizedTrack(title="резус отрицательный", artist="звонкий")
    latinized = normalizer.latinize(track)
    assert latinized.title == "rezus otritsatelnyy"
    assert latinized.artist == "zvonkiy"


def test_latinize_keeps_latin_text_unchanged() -> None:
    normalizer = TrackNormalizer()
    track = NormalizedTrack(title="starboy", artist="the weeknd")
    assert normalizer.latinize(track) == track

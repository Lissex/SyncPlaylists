from syncplaylists.modules.matching.domain.normalization import NormalizedTrack, TrackNormalizer
from syncplaylists.modules.matching.domain.version import VersionTag


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


def test_normalize_extracts_live_version_marker() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize(
        "Swedish House Mafia - Don't You Worry Child (Live at Tomorrowland 2023)"
    )
    assert result.title == "don't you worry child"
    assert "live" not in result.title
    assert result.version.tag is VersionTag.LIVE


def test_normalize_extracts_remix_version_with_remixer() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize("Dua Lipa - Levitating (Tiesto Remix)")
    assert result.title == "levitating"
    assert result.version.tag is VersionTag.REMIX
    assert result.version.remixer == "tiesto"


def test_normalize_does_not_tag_bare_word_in_middle_of_title() -> None:
    normalizer = TrackNormalizer()
    result = normalizer.normalize("Live Forever")
    assert result.version.tag is VersionTag.ORIGINAL
    assert result.title == "live forever"


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


def test_variants_single_result_when_no_dash_and_no_raw_artist() -> None:
    normalizer = TrackNormalizer()
    variants = normalizer.variants("Believer")
    assert len(variants) == 1
    assert variants[0].title == "believer"


def test_variants_includes_dash_split_and_fallback() -> None:
    normalizer = TrackNormalizer()
    variants = normalizer.variants("Макс Корж - Малый напросился", "Музыка | Хиты 2024")
    titles = {v.title for v in variants}
    assert "малый напросился" in titles  # дефис-разбор
    assert "макс корж - малый напросился" in titles  # запасной вариант (title целиком)


def test_variants_includes_swapped_fields_interpretation() -> None:
    normalizer = TrackNormalizer()
    variants = normalizer.variants("The Weeknd", "Starboy")
    swapped = next(v for v in variants if v.title == "starboy")
    assert swapped.artist == "the weeknd"

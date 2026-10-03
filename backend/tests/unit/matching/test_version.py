import pytest

from syncplaylists.modules.matching.domain.version import (
    VersionInfo,
    VersionTag,
    extract_version,
    version_search_suffix,
    versions_match,
)


@pytest.mark.parametrize(
    ("text", "expected_text", "expected_tag"),
    [
        ("starboy (radio edit)", "starboy", VersionTag.RADIO_EDIT),
        ("starboy radio edit", "starboy", VersionTag.RADIO_EDIT),
        ("starboy (extended mix)", "starboy", VersionTag.EXTENDED),
        ("starboy extended", "starboy", VersionTag.EXTENDED),
        ("starboy (acoustic version)", "starboy", VersionTag.ACOUSTIC),
        ("starboy acoustic", "starboy", VersionTag.ACOUSTIC),
        ("starboy (sped up)", "starboy", VersionTag.SPED_UP),
        ("starboy sped up", "starboy", VersionTag.SPED_UP),
        ("starboy (slowed + reverb)", "starboy", VersionTag.SLOWED),
        ("starboy slowed down", "starboy", VersionTag.SLOWED),
        ("starboy (cover)", "starboy", VersionTag.COVER),
        ("starboy cover", "starboy", VersionTag.COVER),
        ("starboy (instrumental)", "starboy", VersionTag.INSTRUMENTAL),
        ("starboy instrumental", "starboy", VersionTag.INSTRUMENTAL),
        ("starboy (karaoke version)", "starboy", VersionTag.KARAOKE),
        ("starboy karaoke", "starboy", VersionTag.KARAOKE),
        ("starboy (live)", "starboy", VersionTag.LIVE),
        ("starboy live", "starboy", VersionTag.LIVE),
        ("starboy", "starboy", VersionTag.ORIGINAL),
    ],
)
def test_extract_version_tags(text: str, expected_text: str, expected_tag: VersionTag) -> None:
    remaining, version = extract_version(text)
    assert remaining == expected_text
    assert version.tag is expected_tag


def test_extract_version_remix_with_named_remixer_in_brackets() -> None:
    remaining, version = extract_version("levitating (tiesto remix)")
    assert remaining == "levitating"
    assert version.tag is VersionTag.REMIX
    assert version.remixer == "tiesto"


def test_extract_version_remix_by_in_brackets() -> None:
    remaining, version = extract_version("starboy (remix by alan walker)")
    assert remaining == "starboy"
    assert version.tag is VersionTag.REMIX
    assert version.remixer == "alan walker"


def test_extract_version_remix_by_trailing() -> None:
    remaining, version = extract_version("starboy remix by alan walker")
    assert remaining == "starboy"
    assert version.tag is VersionTag.REMIX
    assert version.remixer == "alan walker"


def test_extract_version_bare_remix_has_no_remixer() -> None:
    remaining, version = extract_version("starboy (remix)")
    assert remaining == "starboy"
    assert version.tag is VersionTag.REMIX
    assert version.remixer is None


def test_extract_version_extended_mix_is_not_confused_with_remix() -> None:
    remaining, version = extract_version("starboy (extended mix)")
    assert remaining == "starboy"
    assert version.tag is VersionTag.EXTENDED
    assert version.remixer is None


def test_extract_version_compound_bracket_live_acoustic() -> None:
    remaining, version = extract_version("wake me up (live acoustic version)")
    assert remaining == "wake me up"
    assert version.tag is VersionTag.LIVE


def test_extract_version_does_not_tag_bare_word_mid_title() -> None:
    remaining, version = extract_version("live forever")
    assert remaining == "live forever"
    assert version.tag is VersionTag.ORIGINAL


def test_extract_version_does_not_tag_substring_of_another_word() -> None:
    remaining, version = extract_version("alive and well")
    assert remaining == "alive and well"
    assert version.tag is VersionTag.ORIGINAL


@pytest.mark.parametrize(
    ("version", "suffix"),
    [
        (VersionInfo(VersionTag.LIVE), "live"),
        (VersionInfo(VersionTag.REMIX, "tiesto"), "tiesto remix"),
        (VersionInfo(VersionTag.REMIX), "remix"),
        (VersionInfo(VersionTag.ACOUSTIC), "acoustic"),
        (VersionInfo(VersionTag.SPED_UP), "sped up"),
        (VersionInfo(VersionTag.SLOWED), "slowed"),
        (VersionInfo(VersionTag.EXTENDED), "extended mix"),
        (VersionInfo(VersionTag.RADIO_EDIT), "radio edit"),
        (VersionInfo(VersionTag.INSTRUMENTAL), "instrumental"),
        (VersionInfo(), None),
        (VersionInfo(VersionTag.COVER), None),
        (VersionInfo(VersionTag.KARAOKE), None),
    ],
)
def test_version_search_suffix(version: VersionInfo, suffix: str | None) -> None:
    assert version_search_suffix(version) == suffix


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (VersionInfo(), VersionInfo(), True),
        (VersionInfo(VersionTag.LIVE), VersionInfo(), False),
        (VersionInfo(VersionTag.REMIX, "a"), VersionInfo(VersionTag.REMIX, "a"), True),
        (VersionInfo(VersionTag.REMIX, "a"), VersionInfo(VersionTag.REMIX, "b"), False),
        (VersionInfo(VersionTag.REMIX, "a"), VersionInfo(VersionTag.REMIX), True),
    ],
)
def test_versions_match(a: VersionInfo, b: VersionInfo, same: bool) -> None:
    assert versions_match(a, b) is same

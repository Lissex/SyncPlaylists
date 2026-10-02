import pytest

from syncplaylists.modules.matching.domain.transliteration import transliterate


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("звонкий", "zvonkiy"),
        ("резус отрицательный", "rezus otritsatelnyy"),
        ("товарищ", "tovarishch"),
        ("Чиж", "Chizh"),
        ("подъезд", "podezd"),
    ],
)
def test_transliterate_cyrillic(text: str, expected: str) -> None:
    assert transliterate(text) == expected


def test_transliterate_is_passthrough_for_latin_text() -> None:
    assert transliterate("Starboy (Remastered 2022)") == "Starboy (Remastered 2022)"


def test_transliterate_leaves_digits_and_punctuation_untouched() -> None:
    assert transliterate("XO Tour Llif3 - 2024!") == "XO Tour Llif3 - 2024!"

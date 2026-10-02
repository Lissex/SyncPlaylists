from typing import Final

# Практическая транскрипция кириллицы в латиницу (не ГОСТ/ISO, а то, как ру-слова
# обычно транслитерируют на SoundCloud/VK). ъ и ь опускаются.
_CYRILLIC_TO_LATIN: Final[dict[str, str]] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def transliterate(text: str) -> str:
    chars: list[str] = []
    for char in text:
        mapped = _CYRILLIC_TO_LATIN.get(char.lower())
        if mapped is None:
            chars.append(char)
            continue
        chars.append(mapped.capitalize() if char.isupper() else mapped)
    return "".join(chars)

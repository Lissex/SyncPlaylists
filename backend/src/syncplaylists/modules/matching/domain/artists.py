import re
from typing import Final

from rapidfuzz import fuzz

# Негативный lookahead (?!\w) вместо хвостового \b: после необязательной точки \b не
# является границей слова (и "." и пробел — не word-символы), поэтому \bfeat\.?\b
# откатывается и не поглощает точку. (?!\w) поглощает точку корректно и всё равно
# не матчит "featuring".
_ARTIST_SEPARATOR_PATTERN: Final = re.compile(
    r"\s*(?:,|&|\bfeat\.?(?!\w)|\bft\.?(?!\w)|\bx\b|\band\b)\s*"
)


def parse_artist_names(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    parts = _ARTIST_SEPARATOR_PATTERN.split(text)
    return frozenset(part.strip() for part in parts if part.strip())


def artist_set_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    remaining = list(larger)
    matched_total = 0.0
    for name in smaller:
        if not remaining:
            break
        best_index = max(
            range(len(remaining)), key=lambda i: fuzz.token_set_ratio(name, remaining[i])
        )
        matched_total += fuzz.token_set_ratio(name, remaining[best_index]) / 100.0
        del remaining[best_index]
    return matched_total / max(len(a), len(b))

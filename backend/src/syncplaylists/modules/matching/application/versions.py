from typing import Final

from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.version import VersionTag

_SPEED_ALTERED: Final = frozenset({VersionTag.SPED_UP, VersionTag.SLOWED})
_NORMALIZER: Final = TrackNormalizer()


def is_speed_altered(title: str, artist: str | None = None) -> bool:
    """sped up / slowed по названию трека — такие треки не отправляются на распознавание
    по звуку: Shazam на изменённой скорости уверенно ошибается (ARCHITECTURE.md, 11j).
    Публичная точка для других контекстов: matching.domain они не импортируют."""
    return _NORMALIZER.variants(title, artist)[0].version.tag in _SPEED_ALTERED

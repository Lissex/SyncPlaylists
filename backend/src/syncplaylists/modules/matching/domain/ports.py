from typing import Protocol

from syncplaylists.modules.matching.domain.entities import TrackMatch
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform


class TrackMatchRepository(Protocol):
    async def find(
        self, source_ref: ExternalTrackRef, target_platform: Platform
    ) -> TrackMatch | None: ...

    # Глобальный кэш: на (source, target_platform) — одна строка. Если её уже записал
    # кто-то другой (другой перенос/пользователь, параллельная джоба), новая НЕ
    # перезаписывает существующую: побеждает первая запись. Возвращает то, что реально
    # лежит в кэше, — вызывающий код должен работать с ним, а не со своим кандидатом.
    async def save(self, match: TrackMatch) -> TrackMatch: ...

    # Атомарный инкремент confirmations (без read-modify-write).
    async def record_confirmation(
        self, source_ref: ExternalTrackRef, target_platform: Platform
    ) -> None: ...

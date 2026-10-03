from dataclasses import dataclass
from typing import Protocol

from syncplaylists.modules.matching.domain.entities import TrackMatch
from syncplaylists.shared_kernel.domain.base import ValueObject
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


# Заглушки под этап 6 (recognition). Не используются в MatchingPipeline этого этапа.
# Могут переехать в modules/recognition/domain, когда там появятся свои доменные сервисы.


@dataclass(frozen=True, slots=True)
class AudioFragment(ValueObject):
    ref: ExternalTrackRef
    data: bytes


@dataclass(frozen=True, slots=True)
class RecognitionResult(ValueObject):
    title: str
    artist: str
    confidence: float


class AudioRecognizer(Protocol):
    async def recognize(self, fragment: AudioFragment) -> RecognitionResult | None: ...


class FingerprintComparer(Protocol):
    async def similarity(self, a: AudioFragment, b: AudioFragment) -> float: ...

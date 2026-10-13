from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from syncplaylists.modules.recognition.domain.errors import NoAudioReason
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow
from syncplaylists.shared_kernel.domain.base import Entity
from syncplaylists.shared_kernel.domain.search import RecognizedTrack
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef


class RecognitionOutcome(StrEnum):
    RECOGNIZED = "recognized"
    NO_MATCH = "no_match"  # все распознаватели ответили «не знаю»
    NO_AUDIO = "no_audio"  # звука нет (зашифрован, нет потока)


@dataclass(eq=False, slots=True)
class Recognition(Entity):
    """Кэш распознавания трека источника: одна запись на трек. Отрицательные исходы
    живут ограниченно (expires_at) — каталог распознавателей и площадки меняются;
    распознанное — без срока."""

    source_ref: ExternalTrackRef
    outcome: RecognitionOutcome
    created_at: datetime
    expires_at: datetime | None = None
    track: RecognizedTrack | None = None
    window: FragmentWindow | None = None
    no_audio_reason: NoAudioReason | None = None

    def __post_init__(self) -> None:
        if (self.outcome is RecognitionOutcome.RECOGNIZED) != (self.track is not None):
            raise ValueError("Распознанный трек есть только у исхода RECOGNIZED")
        if (self.outcome is RecognitionOutcome.NO_AUDIO) != (self.no_audio_reason is not None):
            raise ValueError("Причина «нет звука» есть только у исхода NO_AUDIO")

    def is_fresh(self, now: datetime) -> bool:
        return self.expires_at is None or now < self.expires_at

    @classmethod
    def recognized(
        cls,
        id: UUID,
        source_ref: ExternalTrackRef,
        track: RecognizedTrack,
        window: FragmentWindow,
        now: datetime,
    ) -> "Recognition":
        return cls(
            id=id,
            source_ref=source_ref,
            outcome=RecognitionOutcome.RECOGNIZED,
            created_at=now,
            track=track,
            window=window,
        )

    @classmethod
    def no_match(
        cls,
        id: UUID,
        source_ref: ExternalTrackRef,
        window: FragmentWindow,
        now: datetime,
        ttl: timedelta,
    ) -> "Recognition":
        return cls(
            id=id,
            source_ref=source_ref,
            outcome=RecognitionOutcome.NO_MATCH,
            created_at=now,
            expires_at=now + ttl,
            window=window,
        )

    @classmethod
    def no_audio(
        cls,
        id: UUID,
        source_ref: ExternalTrackRef,
        reason: NoAudioReason,
        now: datetime,
        ttl: timedelta,
    ) -> "Recognition":
        return cls(
            id=id,
            source_ref=source_ref,
            outcome=RecognitionOutcome.NO_AUDIO,
            created_at=now,
            expires_at=now + ttl,
            no_audio_reason=reason,
        )

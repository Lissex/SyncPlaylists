from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from syncplaylists.modules.transfers.domain.value_objects import MatchResult
from syncplaylists.shared_kernel.domain.base import DomainEvent
from syncplaylists.shared_kernel.domain.search import TrackCandidate


@dataclass(frozen=True, slots=True)
class TransferStarted(DomainEvent):
    transfer_id: UUID


@dataclass(frozen=True, slots=True)
class TrackMatched(DomainEvent):
    transfer_id: UUID
    position: int
    match: MatchResult


@dataclass(frozen=True, slots=True)
class TrackNeedsReview(DomainEvent):
    transfer_id: UUID
    position: int
    candidates: tuple[TrackCandidate, ...]


@dataclass(frozen=True, slots=True)
class TrackNotFound(DomainEvent):
    transfer_id: UUID
    position: int
    candidates: tuple[TrackCandidate, ...]


@dataclass(frozen=True, slots=True)
class CaptchaRequired(DomainEvent):
    transfer_id: UUID
    reason: str


@dataclass(frozen=True, slots=True)
class TransferPausedForQuota(DomainEvent):
    """Площадка попросила подождать дольше разумного повтора — перенос на паузе до
    resume_at (повторный 429 сдвигает срок и даёт событие снова)."""

    transfer_id: UUID
    resume_at: datetime


@dataclass(frozen=True, slots=True)
class TransferPausedForClient(DomainEvent):
    """Операцию должно выполнить браузерное расширение, а оно не может (браузер закрыт,
    нет входа на площадку, капча, ...). Перенос ждёт без срока — продолжится, когда
    расширение сообщит, что готово. reason — ExtensionUnavailableReason."""

    transfer_id: UUID
    reason: str


@dataclass(frozen=True, slots=True)
class TransferResumed(DomainEvent):
    transfer_id: UUID
    status: str  # фаза, в которую вернулись: queued | running | writing


@dataclass(frozen=True, slots=True)
class TransferWritingStarted(DomainEvent):
    transfer_id: UUID


@dataclass(frozen=True, slots=True)
class TransferCompleted(DomainEvent):
    transfer_id: UUID
    total: int
    added: int
    failed: int


@dataclass(frozen=True, slots=True)
class TransferFailed(DomainEvent):
    transfer_id: UUID
    reason: str


@dataclass(frozen=True, slots=True)
class TrackProcessingFailed(DomainEvent):
    """Сопоставление трека не удалось и после всех повторов — item FAILED, перенос
    продолжает остальные треки."""

    transfer_id: UUID
    position: int
    reason: str

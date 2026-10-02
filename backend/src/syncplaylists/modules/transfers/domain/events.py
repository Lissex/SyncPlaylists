from dataclasses import dataclass
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

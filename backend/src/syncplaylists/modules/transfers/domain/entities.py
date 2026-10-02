from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.events import (
    CaptchaRequired,
    TrackMatched,
    TrackNeedsReview,
    TrackNotFound,
    TransferCompleted,
    TransferFailed,
    TransferStarted,
    TransferWritingStarted,
)
from syncplaylists.modules.transfers.domain.value_objects import (
    MatchResult,
    TrackDestination,
    TrackSource,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.base import AggregateRoot, Entity
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    PlaylistRef,
)

_UNRESOLVED_ITEM_STATUSES = (
    TransferItemStatus.PENDING,
    TransferItemStatus.UNCERTAIN,
    TransferItemStatus.NOT_FOUND,
)


@dataclass(eq=False, slots=True)
class TransferItem(Entity):
    transfer_id: UUID
    position: int
    source_track: ExternalTrackRef
    status: TransferItemStatus = TransferItemStatus.PENDING
    match: MatchResult | None = None
    candidates: tuple[TrackCandidate, ...] = ()


@dataclass(eq=False, slots=True)
class Transfer(AggregateRoot):
    user_id: UUID
    source: TrackSource
    destination: TrackDestination
    status: TransferStatus = TransferStatus.QUEUED
    resolved_target: PlaylistRef | None = None
    items: list[TransferItem] = field(default_factory=list)

    def _ensure_status(self, *allowed: TransferStatus) -> None:
        if self.status not in allowed:
            raise InvalidTransferTransitionError(
                f"Transfer {self.id}: операция недопустима в статусе {self.status}"
            )

    def set_resolved_target(self, ref: PlaylistRef) -> None:
        self.resolved_target = ref

    def _item(self, position: int) -> TransferItem:
        for item in self.items:
            if item.position == position:
                return item
        raise InvalidTransferTransitionError(f"TransferItem с position={position} не найден")

    def start(self, now: datetime) -> None:
        self._ensure_status(TransferStatus.QUEUED)
        self.status = TransferStatus.RUNNING
        self.record_event(TransferStarted(occurred_at=now, transfer_id=self.id))

    def add_item(self, position: int, source_track: ExternalTrackRef) -> None:
        self._ensure_status(TransferStatus.RUNNING)
        self.items.append(
            TransferItem(
                id=uuid4(),
                transfer_id=self.id,
                position=position,
                source_track=source_track,
            )
        )

    def record_match(self, position: int, result: MatchResult, now: datetime) -> None:
        self._ensure_status(TransferStatus.RUNNING)
        item = self._item(position)
        if item.status is not TransferItemStatus.PENDING:
            raise InvalidTransferTransitionError(
                f"TransferItem {position} уже обработан (статус {item.status})"
            )
        item.status = TransferItemStatus.MATCHED
        item.match = result
        self.record_event(
            TrackMatched(occurred_at=now, transfer_id=self.id, position=position, match=result)
        )

    def record_uncertain(
        self, position: int, candidates: tuple[TrackCandidate, ...], now: datetime
    ) -> None:
        self._ensure_status(TransferStatus.RUNNING)
        item = self._item(position)
        if item.status is not TransferItemStatus.PENDING:
            raise InvalidTransferTransitionError(
                f"TransferItem {position} уже обработан (статус {item.status})"
            )
        item.status = TransferItemStatus.UNCERTAIN
        item.candidates = candidates
        self.record_event(
            TrackNeedsReview(
                occurred_at=now, transfer_id=self.id, position=position, candidates=candidates
            )
        )

    def record_not_found(
        self, position: int, candidates: tuple[TrackCandidate, ...], now: datetime
    ) -> None:
        self._ensure_status(TransferStatus.RUNNING)
        item = self._item(position)
        if item.status is not TransferItemStatus.PENDING:
            raise InvalidTransferTransitionError(
                f"TransferItem {position} уже обработан (статус {item.status})"
            )
        item.status = TransferItemStatus.NOT_FOUND
        item.candidates = candidates
        self.record_event(
            TrackNotFound(
                occurred_at=now, transfer_id=self.id, position=position, candidates=candidates
            )
        )

    def pause_for_captcha(self, reason: str, now: datetime) -> None:
        self._ensure_status(TransferStatus.RUNNING)
        self.status = TransferStatus.PAUSED_CAPTCHA
        self.record_event(CaptchaRequired(occurred_at=now, transfer_id=self.id, reason=reason))

    def resume(self) -> None:
        self._ensure_status(TransferStatus.PAUSED_CAPTCHA)
        self.status = TransferStatus.RUNNING

    def enter_review(self) -> None:
        self._ensure_status(TransferStatus.RUNNING)
        if any(item.status is TransferItemStatus.PENDING for item in self.items):
            raise InvalidTransferTransitionError("есть необработанные items")
        if not any(
            item.status in (TransferItemStatus.UNCERTAIN, TransferItemStatus.NOT_FOUND)
            for item in self.items
        ):
            raise InvalidTransferTransitionError("нет items, требующих ревью")
        self.status = TransferStatus.REVIEW

    def resolve_item(
        self, position: int, chosen_ref: ExternalTrackRef | None, now: datetime
    ) -> None:
        self._ensure_status(TransferStatus.REVIEW)
        item = self._item(position)
        if item.status not in (TransferItemStatus.UNCERTAIN, TransferItemStatus.NOT_FOUND):
            raise InvalidTransferTransitionError(
                f"TransferItem {position} не ожидает ручного решения (статус {item.status})"
            )
        if chosen_ref is None:
            item.status = TransferItemStatus.FAILED
            return
        result = MatchResult(target_ref=chosen_ref, method="manual", score=MatchScore(1.0))
        item.status = TransferItemStatus.MATCHED
        item.match = result
        self.record_event(
            TrackMatched(occurred_at=now, transfer_id=self.id, position=position, match=result)
        )

    def has_unresolved_items(self) -> bool:
        return any(item.status in _UNRESOLVED_ITEM_STATUSES for item in self.items)

    def begin_writing(self, now: datetime) -> None:
        self._ensure_status(TransferStatus.RUNNING, TransferStatus.REVIEW)
        if self.has_unresolved_items():
            raise InvalidTransferTransitionError("есть нерешённые items")
        self.status = TransferStatus.WRITING
        self.record_event(TransferWritingStarted(occurred_at=now, transfer_id=self.id))

    def mark_added(self, position: int) -> None:
        self._ensure_status(TransferStatus.WRITING)
        item = self._item(position)
        if item.status is not TransferItemStatus.MATCHED:
            raise InvalidTransferTransitionError(
                f"TransferItem {position} не в статусе MATCHED (статус {item.status})"
            )
        item.status = TransferItemStatus.ADDED

    def mark_write_failed(self, position: int) -> None:
        self._ensure_status(TransferStatus.WRITING)
        item = self._item(position)
        item.status = TransferItemStatus.FAILED

    def complete(self, now: datetime) -> None:
        self._ensure_status(TransferStatus.WRITING)
        if any(
            item.status not in (TransferItemStatus.ADDED, TransferItemStatus.FAILED)
            for item in self.items
        ):
            raise InvalidTransferTransitionError("есть items без финального статуса записи")
        self.status = TransferStatus.DONE
        added = sum(1 for item in self.items if item.status is TransferItemStatus.ADDED)
        failed = sum(1 for item in self.items if item.status is TransferItemStatus.FAILED)
        self.record_event(
            TransferCompleted(
                occurred_at=now,
                transfer_id=self.id,
                total=len(self.items),
                added=added,
                failed=failed,
            )
        )

    def fail(self, reason: str, now: datetime) -> None:
        if self.status in (TransferStatus.DONE, TransferStatus.FAILED):
            raise InvalidTransferTransitionError(f"Transfer {self.id} уже завершён")
        self.status = TransferStatus.FAILED
        self.record_event(TransferFailed(occurred_at=now, transfer_id=self.id, reason=reason))

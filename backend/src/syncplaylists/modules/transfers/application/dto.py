from dataclasses import dataclass
from uuid import UUID

from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.value_objects import (
    MatchResult,
    TrackDestination,
    TrackSource,
)
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef


@dataclass(frozen=True, slots=True)
class TransferItemDto:
    position: int
    status: str
    source_track: ExternalTrackRef
    match: MatchResult | None
    candidates: tuple[TrackCandidate, ...]

    @classmethod
    def from_domain(cls, item: TransferItem) -> "TransferItemDto":
        return cls(
            position=item.position,
            status=item.status.value,
            source_track=item.source_track,
            match=item.match,
            candidates=item.candidates,
        )


@dataclass(frozen=True, slots=True)
class TransferDto:
    id: UUID
    user_id: UUID
    status: str
    source: TrackSource
    destination: TrackDestination
    items: tuple[TransferItemDto, ...]

    @classmethod
    def from_domain(cls, transfer: Transfer) -> "TransferDto":
        return cls(
            id=transfer.id,
            user_id=transfer.user_id,
            status=transfer.status.value,
            source=transfer.source,
            destination=transfer.destination,
            items=tuple(TransferItemDto.from_domain(item) for item in transfer.items),
        )

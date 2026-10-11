from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from syncplaylists.modules.transfers.application.ports import ProgressSample
from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.progress import estimate_remaining
from syncplaylists.modules.transfers.domain.value_objects import (
    MatchResult,
    TrackDestination,
    TrackSource,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, PlaylistRef


@dataclass(frozen=True, slots=True)
class SourceTrackMeta:
    """Что искали: трек источника, как его назвала площадка (из catalog)."""

    title: str
    artist: str
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TransferItemDto:
    position: int
    status: str
    source_track: ExternalTrackRef
    match: MatchResult | None
    candidates: tuple[TrackCandidate, ...]
    source_meta: SourceTrackMeta | None = None

    @classmethod
    def from_domain(
        cls, item: TransferItem, source_meta: SourceTrackMeta | None = None
    ) -> "TransferItemDto":
        return cls(
            position=item.position,
            status=item.status.value,
            source_track=item.source_track,
            match=item.match,
            candidates=item.candidates,
            source_meta=source_meta,
        )


@dataclass(frozen=True, slots=True)
class TransferProgressDto:
    status: str
    total: int
    pending: int
    matched: int
    uncertain: int
    not_found: int
    added: int
    failed: int
    skipped: int
    # Оценка оставшегося времени матчинга, секунды. Только для RUNNING; None — пока
    # мало данных или перенос не в фазе матчинга.
    eta_seconds: int | None
    # PAUSED_QUOTA: когда перенос продолжится сам (квота площадки).
    resume_at: datetime | None = None
    # PAUSED_CLIENT: почему ждём браузерное расширение (offline, captcha, ...);
    # PAUSED_QUOTA: почему площадка просит подождать, если известно
    # (vk_library_daily_limit, vk_flood_control).
    pause_reason: str | None = None

    @classmethod
    def from_sample(cls, sample: ProgressSample, now: datetime) -> "TransferProgressDto":
        progress = sample.progress
        eta_seconds: int | None = None
        if sample.status is TransferStatus.RUNNING:
            eta = estimate_remaining(sample.recent_processed_at, progress.pending, now)
            eta_seconds = round(eta.total_seconds()) if eta is not None else None
        return cls(
            status=sample.status.value,
            total=progress.total,
            pending=progress.pending,
            matched=progress.matched,
            uncertain=progress.uncertain,
            not_found=progress.not_found,
            added=progress.added,
            failed=progress.failed,
            skipped=progress.skipped,
            eta_seconds=eta_seconds,
            resume_at=sample.resume_at if sample.status is TransferStatus.PAUSED_QUOTA else None,
            pause_reason=(
                sample.pause_reason
                if sample.status in (TransferStatus.PAUSED_CLIENT, TransferStatus.PAUSED_QUOTA)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class TransferDto:
    id: UUID
    user_id: UUID
    status: str
    source: TrackSource
    destination: TrackDestination
    items: tuple[TransferItemDto, ...]
    progress: TransferProgressDto | None = None
    resolved_targets: tuple[PlaylistRef, ...] = ()
    source_title: str | None = None
    created_at: datetime | None = None
    # Сколько найдено каждым методом сопоставления (isrc, fuzzy, audio, manual, ...).
    match_methods: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def from_domain(
        cls,
        transfer: Transfer,
        progress: TransferProgressDto | None = None,
        *,
        source_meta: Mapping[ExternalTrackRef, SourceTrackMeta] | None = None,
        match_methods: Mapping[str, int] | None = None,
    ) -> "TransferDto":
        meta = source_meta or {}
        return cls(
            id=transfer.id,
            user_id=transfer.user_id,
            status=transfer.status.value,
            source=transfer.source,
            destination=transfer.destination,
            items=tuple(
                TransferItemDto.from_domain(item, meta.get(item.source_track))
                for item in transfer.items
            ),
            progress=progress,
            resolved_targets=transfer.resolved_targets,
            source_title=transfer.source_title,
            created_at=transfer.created_at,
            match_methods=dict(match_methods or {}),
        )

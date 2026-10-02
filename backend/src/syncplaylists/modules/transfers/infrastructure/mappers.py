from pydantic import BaseModel

from syncplaylists.modules.catalog.application.ports import PlatformTrackRepository
from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    FileFormat,
    FileSource,
    LibraryDestination,
    LibrarySource,
    MatchResult,
    NewPlaylist,
    PlaylistSource,
    TrackDestination,
    TrackSource,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.modules.transfers.infrastructure.orm import TransferItemOrm, TransferOrm
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)


class _TrackCandidateDTO(BaseModel):
    """Серилизационная граница для transfer_items.candidates (jsonb) — не часть
    домена, живёт только в mappers.py."""

    platform: str
    external_id: str
    title: str
    artist: str
    duration_ms: int | None = None
    isrc: str | None = None

    @classmethod
    def from_domain(cls, candidate: TrackCandidate) -> "_TrackCandidateDTO":
        return cls(
            platform=candidate.ref.platform.value,
            external_id=candidate.ref.external_id,
            title=candidate.title,
            artist=candidate.artist,
            duration_ms=candidate.duration.milliseconds if candidate.duration else None,
            isrc=candidate.isrc.value if candidate.isrc else None,
        )

    def to_domain(self) -> TrackCandidate:
        return TrackCandidate(
            ref=ExternalTrackRef(Platform(self.platform), self.external_id),
            title=self.title,
            artist=self.artist,
            duration=Duration(self.duration_ms) if self.duration_ms is not None else None,
            isrc=ISRC(self.isrc) if self.isrc is not None else None,
        )


def _source_to_columns(source: TrackSource) -> dict[str, object]:
    if isinstance(source, PlaylistSource):
        return {
            "source_kind": "playlist",
            "source_platform": source.ref.platform.value,
            "source_playlist_id": source.ref.external_id,
        }
    if isinstance(source, LibrarySource):
        return {
            "source_kind": "library",
            "source_platform": source.platform.value,
            "source_account_id": source.account_id,
        }
    assert isinstance(source, FileSource)
    return {
        "source_kind": "file",
        "source_file_id": source.file_id,
        "source_file_format": source.format.value,
    }


def _columns_to_source(orm: TransferOrm) -> TrackSource:
    if orm.source_kind == "playlist":
        assert orm.source_platform is not None
        assert orm.source_playlist_id is not None
        return PlaylistSource(
            ref=PlaylistRef(Platform(orm.source_platform), orm.source_playlist_id)
        )
    if orm.source_kind == "library":
        assert orm.source_platform is not None
        assert orm.source_account_id is not None
        return LibrarySource(
            platform=Platform(orm.source_platform), account_id=orm.source_account_id
        )
    assert orm.source_kind == "file"
    assert orm.source_file_id is not None
    assert orm.source_file_format is not None
    return FileSource(file_id=orm.source_file_id, format=FileFormat(orm.source_file_format))


def _destination_to_columns(destination: TrackDestination) -> dict[str, object]:
    if isinstance(destination, ExistingPlaylist):
        return {
            "destination_kind": "existing",
            "target_platform": destination.ref.platform.value,
            "target_playlist_id": destination.ref.external_id,
        }
    if isinstance(destination, NewPlaylist):
        return {
            "destination_kind": "new",
            "target_platform": destination.platform.value,
            "new_playlist_title": destination.title,
            "new_playlist_description": destination.description,
        }
    assert isinstance(destination, LibraryDestination)
    return {
        "destination_kind": "library",
        "target_platform": destination.platform.value,
        "destination_account_id": destination.account_id,
    }


def _columns_to_destination(orm: TransferOrm) -> TrackDestination:
    if orm.destination_kind == "existing":
        assert orm.target_playlist_id is not None
        return ExistingPlaylist(
            ref=PlaylistRef(Platform(orm.target_platform), orm.target_playlist_id)
        )
    if orm.destination_kind == "new":
        assert orm.new_playlist_title is not None
        return NewPlaylist(
            platform=Platform(orm.target_platform),
            title=orm.new_playlist_title,
            description=orm.new_playlist_description,
        )
    assert orm.destination_kind == "library"
    assert orm.destination_account_id is not None
    return LibraryDestination(
        platform=Platform(orm.target_platform), account_id=orm.destination_account_id
    )


def _resolved_target_to_columns(transfer: Transfer) -> dict[str, object | None]:
    if transfer.resolved_target is None:
        return {"resolved_target_platform": None, "resolved_target_playlist_id": None}
    return {
        "resolved_target_platform": transfer.resolved_target.platform.value,
        "resolved_target_playlist_id": transfer.resolved_target.external_id,
    }


def _columns_to_resolved_target(orm: TransferOrm) -> PlaylistRef | None:
    if orm.resolved_target_platform is None or orm.resolved_target_playlist_id is None:
        return None
    return PlaylistRef(Platform(orm.resolved_target_platform), orm.resolved_target_playlist_id)


async def item_to_orm(
    item: TransferItem, platform_tracks: PlatformTrackRepository
) -> TransferItemOrm:
    source_pt = await platform_tracks.find_by_ref(item.source_track)
    assert source_pt is not None, f"platform_track для {item.source_track} должен существовать"
    return TransferItemOrm(
        id=item.id,
        transfer_id=item.transfer_id,
        position=item.position,
        source_pt_id=source_pt.id,
        status=item.status.value,
        match_target_platform=item.match.target_ref.platform.value if item.match else None,
        match_target_external_id=item.match.target_ref.external_id if item.match else None,
        match_method=item.match.method if item.match else None,
        match_score=item.match.score.value if item.match else None,
        candidates=[_TrackCandidateDTO.from_domain(c).model_dump() for c in item.candidates],
    )


async def item_to_domain(
    orm: TransferItemOrm, platform_tracks: PlatformTrackRepository
) -> TransferItem:
    source_pt = await platform_tracks.find_by_id(orm.source_pt_id)
    assert source_pt is not None, f"platform_track {orm.source_pt_id} не найден"
    match = None
    if orm.match_target_platform is not None:
        assert orm.match_target_external_id is not None
        assert orm.match_method is not None
        assert orm.match_score is not None
        match = MatchResult(
            target_ref=ExternalTrackRef(
                Platform(orm.match_target_platform), orm.match_target_external_id
            ),
            method=orm.match_method,
            score=MatchScore(orm.match_score),
        )
    return TransferItem(
        id=orm.id,
        transfer_id=orm.transfer_id,
        position=orm.position,
        source_track=ExternalTrackRef(source_pt.platform, source_pt.external_id),
        status=TransferItemStatus(orm.status),
        match=match,
        candidates=tuple(_TrackCandidateDTO(**c).to_domain() for c in orm.candidates),
    )


async def transfer_to_orm(
    transfer: Transfer, platform_tracks: PlatformTrackRepository
) -> TransferOrm:
    items = [await item_to_orm(item, platform_tracks) for item in transfer.items]
    return TransferOrm(
        id=transfer.id,
        user_id=transfer.user_id,
        status=transfer.status.value,
        items=items,
        **_source_to_columns(transfer.source),
        **_destination_to_columns(transfer.destination),
        **_resolved_target_to_columns(transfer),
    )


async def transfer_to_domain(
    orm: TransferOrm, platform_tracks: PlatformTrackRepository
) -> Transfer:
    items = [await item_to_domain(item_orm, platform_tracks) for item_orm in orm.items]
    return Transfer(
        id=orm.id,
        user_id=orm.user_id,
        source=_columns_to_source(orm),
        destination=_columns_to_destination(orm),
        status=TransferStatus(orm.status),
        resolved_target=_columns_to_resolved_target(orm),
        items=items,
    )

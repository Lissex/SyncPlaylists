from uuid import UUID

from syncplaylists.modules.catalog.application.ports import PlatformTrackRepository
from syncplaylists.modules.matching.domain.entities import MatchMethod, TrackMatch
from syncplaylists.modules.matching.infrastructure.orm import TrackMatchOrm
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
)


async def match_to_orm(
    match: TrackMatch, platform_tracks: PlatformTrackRepository
) -> TrackMatchOrm:
    source = await platform_tracks.find_by_ref(match.source_ref)
    target = await platform_tracks.find_by_ref(match.target_ref)
    assert source is not None, f"platform_track для {match.source_ref} должен существовать"
    assert target is not None, f"platform_track для {match.target_ref} должен существовать"
    return TrackMatchOrm(
        id=match.id,
        source_pt_id=source.id,
        target_platform=match.target_platform.value,
        target_pt_id=target.id,
        method=match.method.value,
        score=match.score.value,
        confirmations=match.confirmations,
    )


async def match_to_domain(
    orm: TrackMatchOrm, platform_tracks: PlatformTrackRepository
) -> TrackMatch:
    source_ref = await _ref_for(orm.source_pt_id, platform_tracks)
    target_ref = await _ref_for(orm.target_pt_id, platform_tracks)
    return TrackMatch(
        id=orm.id,
        source_ref=source_ref,
        target_platform=Platform(orm.target_platform),
        target_ref=target_ref,
        method=MatchMethod(orm.method),
        score=MatchScore(orm.score),
        confirmations=orm.confirmations,
    )


async def _ref_for(pt_id: UUID, platform_tracks: PlatformTrackRepository) -> ExternalTrackRef:
    track = await platform_tracks.find_by_id(pt_id)
    assert track is not None, f"platform_track {pt_id} не найден"
    return ExternalTrackRef(track.platform, track.external_id)

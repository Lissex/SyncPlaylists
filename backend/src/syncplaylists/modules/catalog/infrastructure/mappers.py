from syncplaylists.modules.catalog.domain.entities import CanonicalTrack, PlatformTrack
from syncplaylists.modules.catalog.infrastructure.orm import CanonicalTrackOrm, PlatformTrackOrm
from syncplaylists.shared_kernel.domain.value_objects import ISRC, Duration, Platform


def canonical_to_domain(orm: CanonicalTrackOrm) -> CanonicalTrack:
    return CanonicalTrack(
        id=orm.id,
        title_norm=orm.title_norm,
        artist_norm=orm.artist_norm,
        duration=Duration(orm.duration_ms) if orm.duration_ms is not None else None,
        isrc=ISRC(orm.isrc) if orm.isrc is not None else None,
        mbid=orm.mbid,
    )


def canonical_to_orm(entity: CanonicalTrack) -> CanonicalTrackOrm:
    return CanonicalTrackOrm(
        id=entity.id,
        isrc=entity.isrc.value if entity.isrc is not None else None,
        title_norm=entity.title_norm,
        artist_norm=entity.artist_norm,
        duration_ms=entity.duration.milliseconds if entity.duration is not None else None,
        mbid=entity.mbid,
    )


def platform_track_to_domain(orm: PlatformTrackOrm) -> PlatformTrack:
    return PlatformTrack(
        id=orm.id,
        platform=Platform(orm.platform),
        external_id=orm.external_id,
        raw_title=orm.raw_title,
        raw_artist=orm.raw_artist,
        duration=Duration(orm.duration_ms) if orm.duration_ms is not None else None,
        isrc=ISRC(orm.isrc) if orm.isrc is not None else None,
        canonical_id=orm.canonical_id,
    )


def platform_track_to_orm(entity: PlatformTrack) -> PlatformTrackOrm:
    return PlatformTrackOrm(
        id=entity.id,
        platform=entity.platform.value,
        external_id=entity.external_id,
        canonical_id=entity.canonical_id,
        raw_title=entity.raw_title,
        raw_artist=entity.raw_artist,
        duration_ms=entity.duration.milliseconds if entity.duration is not None else None,
        isrc=entity.isrc.value if entity.isrc is not None else None,
    )

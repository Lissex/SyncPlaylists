from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.domain.entities import PlatformTrack
from syncplaylists.modules.catalog.infrastructure.repository import SqlPlatformTrackRepository
from syncplaylists.modules.recognition.domain.entities import Recognition, RecognitionOutcome
from syncplaylists.modules.recognition.domain.errors import NoAudioReason
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow
from syncplaylists.modules.recognition.infrastructure.repository import SqlRecognitionRepository
from syncplaylists.shared_kernel.domain.search import RecognizedTrack
from syncplaylists.shared_kernel.domain.value_objects import ISRC, ExternalTrackRef, Platform

_NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


async def _source(session: AsyncSession) -> tuple[SqlRecognitionRepository, ExternalTrackRef]:
    platform_tracks = SqlPlatformTrackRepository(session)
    ref = ExternalTrackRef(Platform.SOUNDCLOUD, f"sc-{uuid4().hex[:8]}")
    await platform_tracks.get_or_create(
        PlatformTrack(
            id=uuid4(),
            platform=ref.platform,
            external_id=ref.external_id,
            raw_title="Lucid Dreams (Remix) ft. Lil Uzi Vert",
            raw_artist="Juice WRLD",
        )
    )
    await session.flush()
    return SqlRecognitionRepository(session, platform_tracks), ref


async def test_recognized_round_trip(session: AsyncSession) -> None:
    repository, ref = await _source(session)
    track = RecognizedTrack(
        title="Lucid Dreams",
        artist="Juice WRLD",
        provider="shazam",
        isrc=ISRC("USUG11800685"),
        provider_ref="407592725",
        exact=True,
        offset_ms=114_111,
        time_skew=8.2e-05,
        frequency_skew=0.0,
    )
    recognition = Recognition.recognized(uuid4(), ref, track, FragmentWindow(111_241, 18_000), _NOW)

    await repository.save(recognition)
    found = await repository.find(ref)

    assert found is not None
    assert found.outcome is RecognitionOutcome.RECOGNIZED
    assert found.track == track
    assert found.window == FragmentWindow(111_241, 18_000)
    assert found.created_at == _NOW
    assert found.expires_at is None


async def test_new_result_replaces_previous(session: AsyncSession) -> None:
    repository, ref = await _source(session)
    stale = Recognition.no_audio(uuid4(), ref, NoAudioReason.ENCRYPTED, _NOW, timedelta(days=7))
    await repository.save(stale)
    track = RecognizedTrack(title="Lucid Dreams", artist="Juice WRLD", provider="acrcloud")
    fresh = Recognition.recognized(
        uuid4(), ref, track, FragmentWindow(0, 18_000), _NOW + timedelta(days=8)
    )

    await repository.save(fresh)
    found = await repository.find(ref)

    assert found is not None
    assert found.id == stale.id  # строка та же — одна на трек источника
    assert found.outcome is RecognitionOutcome.RECOGNIZED
    assert found.no_audio_reason is None
    assert found.track == track
    assert found.expires_at is None


async def test_no_audio_round_trip(session: AsyncSession) -> None:
    repository, ref = await _source(session)
    recognition = Recognition.no_audio(
        uuid4(), ref, NoAudioReason.NO_STREAM, _NOW, timedelta(days=7)
    )

    await repository.save(recognition)
    found = await repository.find(ref)

    assert found is not None
    assert found.no_audio_reason is NoAudioReason.NO_STREAM
    assert found.expires_at == _NOW + timedelta(days=7)
    assert found.window is None


async def test_unknown_track_has_no_recognition(session: AsyncSession) -> None:
    repository, _ = await _source(session)

    assert await repository.find(ExternalTrackRef(Platform.SOUNDCLOUD, "never-seen")) is None

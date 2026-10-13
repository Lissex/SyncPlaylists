"""Распознавание по звуку в переносе на настоящем Postgres (этап 6-2): условные UPDATE
ожидания и итога, потолок фазы ставится один раз, просрочка, снимок услышанного и
пересчёт счётчиков при повторе с ревью."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.catalog.domain.entities import PlatformTrack
from syncplaylists.modules.catalog.infrastructure.repository import SqlPlatformTrackRepository
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    NOTE_TIMEOUT,
    ExistingPlaylist,
    MatchResult,
    PlaylistSource,
    RecognitionState,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.modules.transfers.infrastructure.repository import SqlTransferRepository
from syncplaylists.shared_kernel.domain.search import RecognizedTrack, TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)

_HEARD = RecognizedTrack(
    title="Lucid Dreams",
    artist="Juice WRLD",
    provider="shazam",
    isrc=ISRC("USUG11800685"),
    exact=True,
    time_skew=8.2e-05,
    frequency_skew=0.0,
)
_HINT = TrackCandidate(
    ref=ExternalTrackRef(Platform.YANDEX, "hint"), title="Lucid Dreams", artist="Juice WRLD"
)


async def _transfer(session: AsyncSession, user_id: UUID, tracks: int = 2) -> Transfer:
    platform_tracks = SqlPlatformTrackRepository(session)
    transfer = Transfer(
        id=uuid4(),
        user_id=user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.SOUNDCLOUD, f"u/sets/{uuid4().hex[:6]}")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.YANDEX, "1:3")),
        recognize_audio=True,
    )
    transfer.start(datetime.now(UTC))
    for position in range(tracks):
        ref = ExternalTrackRef(Platform.SOUNDCLOUD, f"sc-{uuid4().hex[:8]}")
        await platform_tracks.get_or_create(
            PlatformTrack(
                id=uuid4(),
                platform=ref.platform,
                external_id=ref.external_id,
                raw_title="lucid dreamz",
                raw_artist="beatstore99",
            )
        )
        transfer.add_item(position, ref)
    await SqlTransferRepository(session, platform_tracks).save(transfer)
    await session.flush()
    return transfer


def _repo(session: AsyncSession) -> SqlTransferRepository:
    return SqlTransferRepository(session, SqlPlatformTrackRepository(session))


async def test_await_ready_and_audio_outcome_round_trip(
    session: AsyncSession, user_id: UUID
) -> None:
    transfer = await _transfer(session, user_id)
    repo = _repo(session)

    item = await repo.get_item(transfer.id, 0)
    assert item is not None
    item.await_recognition(TransferItemStatus.NOT_FOUND, (_HINT,))
    assert await repo.save_item_awaiting_recognition(item)
    assert not await repo.save_item_awaiting_recognition(item)  # повторная доставка

    sample = await repo.progress_sample(transfer.id, recent=0)
    assert sample is not None
    assert sample.recognizing == 1
    assert sample.progress.pending == 2  # трек ещё не решён

    item.recognition_ready(None, _HEARD)
    assert await repo.save_recognition_ready(item)
    assert not await repo.save_recognition_ready(item)  # уже не ждёт

    stored = await repo.get_item(transfer.id, 0)
    assert stored is not None
    assert stored.recognition is not None
    assert stored.recognition.state is RecognitionState.READY
    assert stored.recognition.heard == _HEARD
    assert stored.candidates == (_HINT,)

    match = MatchResult(_HINT.ref, method="audio", score=MatchScore(1.0))
    stored.apply_audio_outcome(TransferItemStatus.MATCHED, match, (_HINT,), "exact")
    progress = await repo.save_item_outcome(stored)
    assert progress is not None
    assert (progress.pending, progress.matched) == (1, 1)
    assert await repo.match_method_counts(transfer.id) == {"audio": 1}

    final = await repo.get_item(transfer.id, 0)
    assert final is not None
    assert final.recognition is not None
    assert (final.recognition.state, final.recognition.note) == (RecognitionState.FINAL, "exact")


async def test_deadline_set_once(session: AsyncSession, user_id: UUID) -> None:
    transfer = await _transfer(session, user_id)
    repo = _repo(session)
    first = datetime.now(UTC) + timedelta(minutes=15)

    assert await repo.start_recognition_phase(transfer.id, first) == (first, True)
    later = first + timedelta(minutes=5)
    assert await repo.start_recognition_phase(transfer.id, later) == (first, False)

    header = await repo.get_header(transfer.id)
    assert header is not None
    assert header.recognition_deadline_at == first
    assert header.recognize_audio


async def test_expire_releases_only_waiting_pending_items(
    session: AsyncSession, user_id: UUID
) -> None:
    transfer = await _transfer(session, user_id)
    repo = _repo(session)
    for position in (0, 1):
        item = await repo.get_item(transfer.id, position)
        assert item is not None
        item.await_recognition(TransferItemStatus.UNCERTAIN, (_HINT,))
        await repo.save_item_awaiting_recognition(item)
    done = await repo.get_item(transfer.id, 1)
    assert done is not None
    done.recognition_ready(None, _HEARD)
    await repo.save_recognition_ready(done)

    assert await repo.expire_recognition(transfer.id, NOTE_TIMEOUT) == [0]

    expired = await repo.get_item(transfer.id, 0)
    assert expired is not None
    assert expired.recognition is not None
    assert expired.recognition.state is RecognitionState.READY
    assert expired.recognition.note == NOTE_TIMEOUT
    assert expired.recognition.text_status is TransferItemStatus.UNCERTAIN
    assert expired.candidates == (_HINT,)
    assert await repo.expire_recognition(transfer.id, NOTE_TIMEOUT) == []


async def test_retry_on_review_recounts_on_full_save(session: AsyncSession, user_id: UUID) -> None:
    transfer = await _transfer(session, user_id, tracks=1)
    repo = _repo(session)
    item = await repo.get_item(transfer.id, 0)
    assert item is not None
    item.await_recognition(TransferItemStatus.NOT_FOUND, ())
    await repo.save_item_awaiting_recognition(item)
    item.recognition_ready(NOTE_TIMEOUT, None)
    await repo.save_recognition_ready(item)
    item.apply_text_result()
    await repo.save_item_outcome(item)
    assert await repo.transition_status(transfer.id, TransferStatus.RUNNING, TransferStatus.REVIEW)

    loaded = await repo.get_for_update(transfer.id)
    assert loaded is not None
    loaded.request_recognition_retry(0)
    await repo.save(loaded)
    await session.flush()
    sample = await repo.progress_sample(transfer.id, recent=0)
    assert sample is not None
    assert sample.recognizing == 1

    loaded = await repo.get_for_update(transfer.id)
    assert loaded is not None
    loaded.items[0].recognition_ready(None, _HEARD)
    match = MatchResult(_HINT.ref, method="audio", score=MatchScore(1.0))
    loaded.apply_retried_recognition(
        0, TransferItemStatus.MATCHED, match, (_HINT,), "exact", datetime.now(UTC)
    )
    await repo.save(loaded)
    await session.flush()

    sample = await repo.progress_sample(transfer.id, recent=0)
    assert sample is not None
    assert (sample.progress.matched, sample.progress.not_found, sample.recognizing) == (1, 0, 0)

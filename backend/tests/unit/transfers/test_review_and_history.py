"""Этап 5-0: явная запись после ревью, страницы треков с названиями источника, история."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from syncplaylists.modules.catalog.domain.entities import PlatformTrack
from syncplaylists.modules.transfers.application.ports import TransferCursor
from syncplaylists.modules.transfers.application.use_cases import (
    GetTransferUseCase,
    ListTransferItemsUseCase,
    ListTransfersUseCase,
    StartWritingUseCase,
    TransferNotFoundError,
)
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    MatchResult,
    PlaylistSource,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    Duration,
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)
from tests.fakes import (
    FakePlatformTrackRepository,
    FakeTaskQueue,
    FakeTransferRepository,
    FakeUnitOfWork,
)

_NOW = datetime(2026, 10, 11, tzinfo=UTC)
_USER = uuid4()


def _candidate(external_id: str) -> TrackCandidate:
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, external_id), title="Gruppa Krovi", artist="Kino"
    )


def _transfer(created_at: datetime = _NOW, title: str | None = "Летний вайб") -> Transfer:
    return Transfer(
        id=uuid4(),
        user_id=_USER,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
        source_title=title,
        created_at=created_at,
    )


def _in_review() -> Transfer:
    """0 — найден автоматически, 1 — сомнительный, 2 — не найден."""
    transfer = _transfer()
    transfer.start(_NOW)
    for position in range(3):
        transfer.add_item(position, ExternalTrackRef(Platform.VK, f"src-{position}"))
    transfer.record_match(
        0,
        MatchResult(_candidate("auto").ref, method="isrc", score=MatchScore(1.0)),
        _NOW,
    )
    transfer.record_uncertain(1, (_candidate("c1"),), _NOW)
    transfer.record_not_found(2, (), _NOW)
    transfer.enter_review()
    return transfer


class _Env:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.transfers = FakeTransferRepository()
        self.task_queue = FakeTaskQueue()
        self.platform_tracks = FakePlatformTrackRepository()

    async def seed_source_track(self, external_id: str, title: str, artist: str) -> None:
        await self.platform_tracks.get_or_create(
            PlatformTrack(
                id=uuid4(),
                platform=Platform.VK,
                external_id=external_id,
                raw_title=title,
                raw_artist=artist,
                duration=Duration(285_000),
            )
        )


async def test_start_writing_moves_review_to_writing_and_enqueues_write() -> None:
    env = _Env()
    transfer = _in_review()
    await env.transfers.save(transfer)

    await StartWritingUseCase(env.uow, env.transfers, env.task_queue).execute(_USER, transfer.id)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.WRITING
    statuses = [item.status for item in stored.items]
    assert statuses == [
        TransferItemStatus.MATCHED,
        TransferItemStatus.SKIPPED,  # сомнительный без решения — пропущен
        TransferItemStatus.NOT_FOUND,  # ненайденный остаётся ненайденным
    ]
    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]


async def test_start_writing_twice_is_rejected_without_second_write() -> None:
    env = _Env()
    transfer = _in_review()
    await env.transfers.save(transfer)
    use_case = StartWritingUseCase(env.uow, env.transfers, env.task_queue)
    await use_case.execute(_USER, transfer.id)

    with pytest.raises(InvalidTransferTransitionError):
        await use_case.execute(_USER, transfer.id)

    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]


async def test_start_writing_of_foreign_transfer_is_not_found() -> None:
    env = _Env()
    transfer = _in_review()
    await env.transfers.save(transfer)

    with pytest.raises(TransferNotFoundError):
        await StartWritingUseCase(env.uow, env.transfers, env.task_queue).execute(
            uuid4(), transfer.id
        )


async def test_list_items_filters_by_status_and_adds_source_titles() -> None:
    env = _Env()
    transfer = _in_review()
    await env.transfers.save(transfer)
    await env.seed_source_track("src-1", "Группа крови", "Кино")
    use_case = ListTransferItemsUseCase(env.transfers, env.platform_tracks)

    page = await use_case.execute(
        _USER, transfer.id, [TransferItemStatus.UNCERTAIN, TransferItemStatus.NOT_FOUND]
    )

    assert page is not None
    assert [item.position for item in page] == [1, 2]
    assert page[0].source_meta is not None
    assert (page[0].source_meta.title, page[0].source_meta.artist) == ("Группа крови", "Кино")
    assert page[0].source_meta.duration_ms == 285_000
    assert page[1].source_meta is None  # трека нет в catalog — сайт покажет ref

    after = await use_case.execute(_USER, transfer.id, (), after_position=0, limit=1)
    assert after is not None
    assert [item.position for item in after] == [1]


async def test_list_items_of_foreign_transfer_is_none() -> None:
    env = _Env()
    transfer = _in_review()
    await env.transfers.save(transfer)

    use_case = ListTransferItemsUseCase(env.transfers, env.platform_tracks)
    assert await use_case.execute(uuid4(), transfer.id) is None


async def test_get_transfer_reports_match_methods_and_title() -> None:
    env = _Env()
    transfer = _in_review()
    transfer.resolve_item(1, _candidate("c1").ref, _NOW)
    await env.transfers.save(transfer)

    dto = await GetTransferUseCase(env.transfers, env.platform_tracks).execute(_USER, transfer.id)

    assert dto is not None
    assert dto.source_title == "Летний вайб"
    assert dto.match_methods == {"isrc": 1, "manual": 1}


async def test_get_transfer_header_only_has_no_items_but_counts() -> None:
    env = _Env()
    transfer = _in_review()
    await env.transfers.save(transfer)

    dto = await GetTransferUseCase(env.transfers, env.platform_tracks).execute(
        _USER, transfer.id, with_items=False
    )

    assert dto is not None
    assert dto.items == ()
    assert dto.progress is not None
    assert (dto.progress.total, dto.progress.uncertain, dto.progress.not_found) == (3, 1, 1)


async def test_history_is_newest_first_with_keyset_cursor() -> None:
    env = _Env()
    old, mid, new = (_transfer(_NOW + timedelta(minutes=m)) for m in (0, 1, 2))
    for transfer in (mid, old, new):
        await env.transfers.save(transfer)
    other_user = _transfer()
    other_user.user_id = uuid4()
    await env.transfers.save(other_user)
    use_case = ListTransfersUseCase(env.transfers)

    first = await use_case.execute(_USER, limit=2)
    assert [s.id for s in first] == [new.id, mid.id]
    assert first[0].source_title == "Летний вайб"

    cursor = TransferCursor(created_at=first[-1].created_at, id=first[-1].id)
    rest = await use_case.execute(_USER, before=cursor, limit=2)
    assert [s.id for s in rest] == [old.id]

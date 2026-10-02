from datetime import UTC, datetime, timedelta
from uuid import uuid4

from syncplaylists.modules.transfers.application.use_cases import SweepStaleTransfersUseCase
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import ExistingPlaylist, PlaylistSource
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform, PlaylistRef
from tests.fakes import FakeTaskQueue, FakeTransferRepository

_STALE_AFTER = timedelta(minutes=10)


def _transfer() -> Transfer:
    return Transfer(
        id=uuid4(),
        user_id=uuid4(),
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
    )


async def test_requeues_stale_queued_transfer() -> None:
    transfers = FakeTransferRepository()
    task_queue = FakeTaskQueue()
    transfer = _transfer()
    await transfers.save(transfer)
    transfers.mark_stale(transfer.id, datetime.now(UTC) - timedelta(minutes=20))

    use_case = SweepStaleTransfersUseCase(transfers, task_queue, stale_after=_STALE_AFTER)
    await use_case.execute()

    assert task_queue.enqueued == [("run_transfer", (transfer.id,))]


async def test_requeues_pending_items_of_stale_running_transfer() -> None:
    transfers = FakeTransferRepository()
    task_queue = FakeTaskQueue()
    transfer = _transfer()
    transfer.start(datetime.now(UTC))
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.add_item(1, ExternalTrackRef(Platform.VK, "src-2"))
    await transfers.save(transfer)
    transfers.mark_stale(transfer.id, datetime.now(UTC) - timedelta(minutes=20))

    use_case = SweepStaleTransfersUseCase(transfers, task_queue, stale_after=_STALE_AFTER)
    await use_case.execute()

    assert task_queue.enqueued == [
        ("run_match", (transfer.id, 0)),
        ("run_match", (transfer.id, 1)),
    ]


async def test_does_not_requeue_already_matched_items_of_stale_running_transfer() -> None:
    transfers = FakeTransferRepository()
    task_queue = FakeTaskQueue()
    transfer = _transfer()
    now = datetime.now(UTC)
    transfer.start(now)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.add_item(1, ExternalTrackRef(Platform.VK, "src-2"))
    transfer.record_not_found(0, (), now)  # не PENDING — трогать не нужно
    await transfers.save(transfer)
    transfers.mark_stale(transfer.id, datetime.now(UTC) - timedelta(minutes=20))

    use_case = SweepStaleTransfersUseCase(transfers, task_queue, stale_after=_STALE_AFTER)
    await use_case.execute()

    assert task_queue.enqueued == [("run_match", (transfer.id, 1))]


async def test_ignores_fresh_transfers() -> None:
    transfers = FakeTransferRepository()
    task_queue = FakeTaskQueue()
    transfer = _transfer()
    await transfers.save(transfer)  # updated_at только что выставлен save() — не stale

    use_case = SweepStaleTransfersUseCase(transfers, task_queue, stale_after=_STALE_AFTER)
    await use_case.execute()

    assert task_queue.enqueued == []


async def test_ignores_transfers_in_review_status() -> None:
    transfers = FakeTransferRepository()
    task_queue = FakeTaskQueue()
    transfer = _transfer()
    now = datetime.now(UTC)
    transfer.start(now)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_not_found(0, (), now)
    transfer.enter_review()
    await transfers.save(transfer)
    transfers.mark_stale(transfer.id, datetime.now(UTC) - timedelta(minutes=20))

    use_case = SweepStaleTransfersUseCase(transfers, task_queue, stale_after=_STALE_AFTER)
    await use_case.execute()

    assert task_queue.enqueued == []

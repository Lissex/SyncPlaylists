from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from arq.worker import Retry

from syncplaylists.modules.transfers.application.use_cases import (
    FailTransferItemUseCase,
    MatchTransferItemUseCase,
)
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    MatchResult,
    PlaylistSource,
    TransferItemStatus,
    TransferProgress,
    TransferStatus,
)
from syncplaylists.modules.transfers.presentation.tasks import MATCH_MAX_TRIES, match_with_retries
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)
from tests.fakes import FakeEventPublisher, FakeUnitOfWork
from tests.unit.transfers.test_use_cases import _SPOTIFY_MATCH_1, _VK_TRACK_1, Env

_NOW = datetime(2026, 10, 3, tzinfo=UTC)


def _running_transfer(env: Env, positions: int) -> Transfer:
    transfer = Transfer(
        id=uuid4(),
        user_id=env.user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
    )
    transfer.start(_NOW)
    for position in range(positions):
        transfer.add_item(position, ExternalTrackRef(Platform.VK, f"src-{position}"))
    transfer.pull_domain_events()
    return transfer


# --- домен ---


def test_progress_counts_statuses() -> None:
    progress = TransferProgress.from_statuses(
        [TransferItemStatus.PENDING, TransferItemStatus.MATCHED, TransferItemStatus.MATCHED]
    )

    assert progress == TransferProgress(total=3, pending=1, matched=2)


@pytest.mark.parametrize(
    ("progress", "expected"),
    [
        (TransferProgress(total=2, pending=1, matched=1), None),
        (TransferProgress(total=2, matched=1, not_found=1), TransferStatus.REVIEW),
        (TransferProgress(total=2, matched=1, uncertain=1), TransferStatus.REVIEW),
        (TransferProgress(total=2, matched=1, failed=1), TransferStatus.WRITING),
        (TransferProgress(total=0), TransferStatus.WRITING),
    ],
)
def test_status_after_matching(progress: TransferProgress, expected: TransferStatus | None) -> None:
    assert progress.status_after_matching() is expected


def test_finish_matching_requires_no_pending() -> None:
    env = Env()
    transfer = _running_transfer(env, 1)

    with pytest.raises(InvalidTransferTransitionError):
        transfer.finish_matching(TransferProgress(total=1, pending=1), _NOW)


def test_item_can_leave_pending_only_once() -> None:
    env = Env()
    item = _running_transfer(env, 1).items[0]
    item.apply_not_found(())

    with pytest.raises(InvalidTransferTransitionError):
        item.apply_match(MatchResult(_SPOTIFY_MATCH_1.ref, method="isrc", score=MatchScore(1.0)))


# --- run_match: точечное сохранение ---


async def test_match_saves_single_item_not_whole_aggregate() -> None:
    env = Env()
    transfer = _running_transfer(env, 2)
    transfer.items[0].source_track = _VK_TRACK_1.ref
    await env.seed_source_platform_track(_VK_TRACK_1)
    await env.seed_transfer(transfer)
    env.register_target_search_results(Platform.SPOTIFY, [_SPOTIFY_MATCH_1])
    saves_before = env.transfers.save_calls

    await env.match_transfer_item_use_case().execute(transfer.id, 0)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert env.transfers.save_calls == saves_before  # агрегат целиком не сохранялся
    assert env.transfers.item_outcome_calls == 1
    assert stored.items[0].status is TransferItemStatus.MATCHED
    assert stored.items[1].status is TransferItemStatus.PENDING
    assert stored.status is TransferStatus.RUNNING  # остался pending — перехода нет
    assert env.task_queue.enqueued == []


async def test_last_matched_item_moves_transfer_to_review_when_something_not_found() -> None:
    env = Env()
    transfer = _running_transfer(env, 2)
    transfer.items[0].source_track = _VK_TRACK_1.ref
    transfer.items[0].apply_not_found(())  # уже обработан раньше
    transfer.items[1].source_track = _VK_TRACK_1.ref
    await env.seed_source_platform_track(_VK_TRACK_1)
    await env.seed_transfer(transfer)
    env.register_target_search_results(Platform.SPOTIFY, [_SPOTIFY_MATCH_1])

    await env.match_transfer_item_use_case().execute(transfer.id, 1)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.REVIEW
    assert env.task_queue.enqueued == []


# --- исчерпанные повторы ---


def _fail_use_case(env: Env, publisher: FakeEventPublisher) -> FailTransferItemUseCase:
    env.uow = FakeUnitOfWork(publisher)
    return FailTransferItemUseCase(env.uow, env.transfers, env.task_queue)


async def test_failed_item_does_not_block_rest_of_transfer() -> None:
    env = Env()
    transfer = _running_transfer(env, 2)
    transfer.record_match(
        0, MatchResult(_SPOTIFY_MATCH_1.ref, method="isrc", score=MatchScore(1.0)), _NOW
    )
    transfer.pull_domain_events()
    await env.seed_transfer(transfer)
    publisher = FakeEventPublisher()

    await _fail_use_case(env, publisher).execute(transfer.id, 1, reason="TimeoutError")

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.items[1].status is TransferItemStatus.FAILED
    assert stored.status is TransferStatus.WRITING
    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]
    assert [p["type"] for _, p in publisher.published] == [
        "TrackProcessingFailed",
        "TransferWritingStarted",
    ]


async def test_fail_item_is_noop_for_already_processed_item() -> None:
    env = Env()
    transfer = _running_transfer(env, 2)
    transfer.record_not_found(0, (), _NOW)
    await env.seed_transfer(transfer)

    await _fail_use_case(env, FakeEventPublisher()).execute(transfer.id, 0, reason="x")

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.items[0].status is TransferItemStatus.NOT_FOUND


async def test_fail_item_is_noop_when_transfer_already_failed() -> None:
    env = Env()
    transfer = _running_transfer(env, 1)
    transfer.fail("account_unavailable", _NOW)
    await env.seed_transfer(transfer)

    await _fail_use_case(env, FakeEventPublisher()).execute(transfer.id, 0, reason="x")

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.items[0].status is TransferItemStatus.PENDING


# --- политика повторов run_match ---


class _FailingMatch:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, transfer_id: UUID, position: int) -> None:
        self.calls += 1
        raise RuntimeError("площадка недоступна")


class _RecordingFail:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int, str]] = []

    async def execute(self, transfer_id: UUID, position: int, reason: str) -> None:
        self.calls.append((transfer_id, position, reason))


@pytest.mark.parametrize("job_try", range(1, MATCH_MAX_TRIES))
async def test_run_match_retries_until_tries_exhausted(job_try: int) -> None:
    fail_item = _RecordingFail()

    with pytest.raises(Retry):
        await match_with_retries(
            job_try,
            uuid4(),
            0,
            cast(MatchTransferItemUseCase, _FailingMatch()),
            cast(FailTransferItemUseCase, fail_item),
        )
    assert fail_item.calls == []


async def test_run_match_marks_item_failed_after_last_try() -> None:
    fail_item = _RecordingFail()
    transfer_id = uuid4()

    await match_with_retries(
        MATCH_MAX_TRIES,
        transfer_id,
        3,
        cast(MatchTransferItemUseCase, _FailingMatch()),
        cast(FailTransferItemUseCase, fail_item),
    )

    assert fail_item.calls == [(transfer_id, 3, "RuntimeError")]

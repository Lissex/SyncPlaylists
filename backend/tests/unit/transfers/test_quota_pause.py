"""PAUSED_QUOTA: квота площадки ставит на паузу весь перенос до Retry-After, а
resume_transfer продолжает с места остановки — ни один трек не уходит в FAILED из-за
квоты (этап 4b-3). Только фейки, без реальной площадки."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from syncplaylists.modules.transfers.application.use_cases import (
    PauseTransferForQuotaUseCase,
    ResumeTransferUseCase,
    SweepStaleTransfersUseCase,
)
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.events import TransferPausedForQuota, TransferResumed
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    MatchResult,
    PlaylistSource,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.base import DomainEvent
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)
from tests.fakes import FakeUnitOfWork
from tests.unit.transfers.test_use_cases import Env


class _EventsUnitOfWork(FakeUnitOfWork):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[DomainEvent] = []

    async def commit(self) -> None:
        for aggregate in self._tracked:
            self.events.extend(aggregate.pull_domain_events())
        await super().commit()


def _env() -> Env:
    env = Env()
    env.uow = _EventsUnitOfWork()
    return env


def _events(env: Env) -> list[DomainEvent]:
    return cast(_EventsUnitOfWork, env.uow).events


async def _running(env: Env, matched: int = 1, pending: int = 2) -> Transfer:
    transfer = Transfer(
        id=uuid4(),
        user_id=env.user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
    )
    now = datetime.now(UTC)
    transfer.start(now)
    for position in range(matched + pending):
        transfer.add_item(position, ExternalTrackRef(Platform.VK, f"s{position}"))
    for position in range(matched):
        result = MatchResult(
            target_ref=ExternalTrackRef(Platform.SPOTIFY, f"t{position}"),
            method="fuzzy",
            score=MatchScore(0.95),
        )
        transfer.record_match(position, result, now)
    transfer.pull_domain_events()
    await env.seed_transfer(transfer)
    return transfer


def _pause(env: Env) -> PauseTransferForQuotaUseCase:
    return PauseTransferForQuotaUseCase(env.uow, env.transfers, env.task_queue)


def _resume(env: Env) -> ResumeTransferUseCase:
    return ResumeTransferUseCase(env.uow, env.transfers, env.task_queue)


async def _stored(env: Env, transfer: Transfer) -> Transfer:
    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    return stored


async def test_quota_pauses_whole_transfer_and_schedules_resume() -> None:
    env = _env()
    transfer = await _running(env)

    await _pause(env).execute(transfer.id, 600)

    stored = await _stored(env, transfer)
    assert stored.status is TransferStatus.PAUSED_QUOTA
    assert stored.paused_from is TransferStatus.RUNNING
    assert stored.resume_at is not None
    assert [(task, args) for task, _, args in env.task_queue.scheduled] == [
        ("resume_transfer", (transfer.id,))
    ]
    assert env.task_queue.scheduled[0][1] == stored.resume_at
    paused = [e for e in _events(env) if isinstance(e, TransferPausedForQuota)]
    assert [e.resume_at for e in paused] == [stored.resume_at]


async def test_shorter_second_429_does_not_shorten_pause() -> None:
    env = _env()
    transfer = await _running(env)
    await _pause(env).execute(transfer.id, 600)
    first = (await _stored(env, transfer)).resume_at

    await _pause(env).execute(transfer.id, 30)

    assert (await _stored(env, transfer)).resume_at == first
    assert len(env.task_queue.scheduled) == 1  # тот же срок — та же задача
    assert len([e for e in _events(env) if isinstance(e, TransferPausedForQuota)]) == 1


async def test_match_jobs_do_nothing_while_paused() -> None:
    env = _env()
    transfer = await _running(env)
    await _pause(env).execute(transfer.id, 600)

    await env.match_transfer_item_use_case().execute(transfer.id, 1)

    stored = await _stored(env, transfer)
    assert stored.items[1].status is TransferItemStatus.PENDING


async def test_resume_before_deadline_is_noop() -> None:
    env = _env()
    transfer = await _running(env)
    await _pause(env).execute(transfer.id, 600)

    await _resume(env).execute(transfer.id)

    assert (await _stored(env, transfer)).status is TransferStatus.PAUSED_QUOTA
    assert env.task_queue.enqueued == []


async def test_resume_requeues_only_pending_tracks() -> None:
    env = _env()
    transfer = await _running(env, matched=1, pending=2)
    await _pause(env).execute(transfer.id, 600)
    (await _stored(env, transfer)).resume_at = datetime.now(UTC) - timedelta(seconds=1)

    await _resume(env).execute(transfer.id)

    stored = await _stored(env, transfer)
    assert (stored.status, stored.resume_at, stored.paused_from) == (
        TransferStatus.RUNNING,
        None,
        None,
    )
    assert env.task_queue.enqueued == [
        ("run_match", (transfer.id, 1)),
        ("run_match", (transfer.id, 2)),
    ]
    assert any(isinstance(e, TransferResumed) for e in _events(env))

    await _resume(env).execute(transfer.id)  # повторная доставка — no-op
    assert len(env.task_queue.enqueued) == 2


async def test_resume_finishes_matching_if_last_tracks_landed_during_pause() -> None:
    env = _env()
    transfer = await _running(env, matched=2, pending=0)
    await _pause(env).execute(transfer.id, 600)
    (await _stored(env, transfer)).resume_at = datetime.now(UTC) - timedelta(seconds=1)

    await _resume(env).execute(transfer.id)

    assert (await _stored(env, transfer)).status is TransferStatus.WRITING
    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]


async def test_resume_writing_phase_requeues_write() -> None:
    env = _env()
    transfer = await _running(env, matched=1, pending=0)
    stored = await _stored(env, transfer)
    stored.status = TransferStatus.WRITING
    await _pause(env).execute(transfer.id, 600)
    stored.resume_at = datetime.now(UTC) - timedelta(seconds=1)

    await _resume(env).execute(transfer.id)

    assert stored.status is TransferStatus.WRITING
    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]


async def test_pause_ignored_for_finished_transfer() -> None:
    env = _env()
    transfer = await _running(env)
    (await _stored(env, transfer)).status = TransferStatus.DONE

    await _pause(env).execute(transfer.id, 600)

    assert (await _stored(env, transfer)).status is TransferStatus.DONE
    assert env.task_queue.scheduled == []


async def test_sweeper_resumes_overdue_pause() -> None:
    env = _env()
    transfer = await _running(env)
    await _pause(env).execute(transfer.id, 600)
    (await _stored(env, transfer)).resume_at = datetime.now(UTC) - timedelta(hours=1)

    await SweepStaleTransfersUseCase(env.transfers, env.task_queue).execute()

    assert ("resume_transfer", (transfer.id,)) in env.task_queue.enqueued

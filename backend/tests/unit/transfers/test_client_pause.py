"""PAUSED_CLIENT: операцию на площадке выполняет браузерное расширение, а оно сейчас не
может — перенос ждёт браузер (пауза, а не FAILED) и продолжается по сигналу
«расширение готово» (этап 4c). Только фейки."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from syncplaylists.modules.transfers.application.use_cases import (
    FailTransferItemUseCase,
    FailTransferUseCase,
    MatchTransferItemUseCase,
    PauseTransferForClientUseCase,
    PauseTransferForQuotaUseCase,
    ResumeClientPausedTransfersUseCase,
    WriteTransferUseCase,
)
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.events import TransferPausedForClient, TransferResumed
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    MatchResult,
    NewPlaylist,
    PlaylistSource,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.modules.transfers.presentation.tasks import (
    match_with_retries,
    with_platform_retries,
)
from syncplaylists.shared_kernel.domain.base import DomainEvent
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    ExtensionUnavailableReason,
)
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)
from tests.fakes import FakeMusicPlatformGateway, FakeUnitOfWork
from tests.fakes.transfers import RecordingPause
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


async def _running(
    env: Env, matched: int = 1, pending: int = 2, destination_platform: Platform = Platform.SPOTIFY
) -> Transfer:
    transfer = Transfer(
        id=uuid4(),
        user_id=env.user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=ExistingPlaylist(ref=PlaylistRef(destination_platform, "dst")),
    )
    now = datetime.now(UTC)
    transfer.start(now)
    for position in range(matched + pending):
        transfer.add_item(position, ExternalTrackRef(Platform.VK, f"s{position}"))
    for position in range(matched):
        transfer.record_match(
            position,
            MatchResult(
                target_ref=ExternalTrackRef(destination_platform, f"t{position}"),
                method="isrc",
                score=MatchScore(1.0),
            ),
            now,
        )
    transfer.pull_domain_events()
    await env.seed_transfer(transfer)
    return transfer


def _pause(env: Env) -> PauseTransferForClientUseCase:
    return PauseTransferForClientUseCase(env.uow, env.transfers)


def _resume(env: Env) -> ResumeClientPausedTransfersUseCase:
    return ResumeClientPausedTransfersUseCase(env.uow, env.transfers, env.task_queue)


async def _stored(env: Env, transfer_id: UUID) -> Transfer:
    stored = await env.transfers.get(transfer_id)
    assert stored is not None
    return stored


async def test_pause_waits_without_deadline_and_keeps_phase() -> None:
    env = _env()
    transfer = await _running(env)

    await _pause(env).execute(transfer.id, "offline")

    stored = await _stored(env, transfer.id)
    assert stored.status is TransferStatus.PAUSED_CLIENT
    assert stored.paused_from is TransferStatus.RUNNING
    assert stored.pause_reason == "offline"
    assert env.task_queue.scheduled == []  # без срока — ждём сигнала, а не таймера
    paused = [e for e in _events(env) if isinstance(e, TransferPausedForClient)]
    assert [e.reason for e in paused] == ["offline"]


async def test_repeated_pause_same_reason_no_new_event_new_reason_updates() -> None:
    env = _env()
    transfer = await _running(env)

    await _pause(env).execute(transfer.id, "offline")
    await _pause(env).execute(transfer.id, "offline")
    await _pause(env).execute(transfer.id, "captcha")

    stored = await _stored(env, transfer.id)
    assert stored.pause_reason == "captcha"
    assert stored.paused_from is TransferStatus.RUNNING
    reasons = [e.reason for e in _events(env) if isinstance(e, TransferPausedForClient)]
    assert reasons == ["offline", "captcha"]


async def test_match_jobs_do_nothing_while_waiting_for_browser() -> None:
    env = _env()
    transfer = await _running(env)
    await _pause(env).execute(transfer.id, "offline")

    await env.match_transfer_item_use_case().execute(transfer.id, 1)

    assert (await _stored(env, transfer.id)).items[1].status is TransferItemStatus.PENDING


async def test_resume_requeues_only_pending_tracks_for_that_platform() -> None:
    env = _env()
    transfer = await _running(env, matched=1, pending=2)
    other = await _running(env, destination_platform=Platform.YANDEX)
    await _pause(env).execute(transfer.id, "offline")
    await _pause(env).execute(other.id, "offline")

    await _resume(env).execute(env.user_id, Platform.SPOTIFY)

    stored = await _stored(env, transfer.id)
    assert (stored.status, stored.paused_from, stored.pause_reason) == (
        TransferStatus.RUNNING,
        None,
        None,
    )
    assert env.task_queue.enqueued == [
        ("run_match", (transfer.id, 1)),
        ("run_match", (transfer.id, 2)),
    ]
    assert any(isinstance(e, TransferResumed) for e in _events(env))
    # Перенос на другую площадку ждёт своего сигнала.
    assert (await _stored(env, other.id)).status is TransferStatus.PAUSED_CLIENT

    await _resume(env).execute(env.user_id, Platform.SPOTIFY)  # повторный сигнал — no-op
    assert len(env.task_queue.enqueued) == 2


async def test_resume_matches_source_platform_too() -> None:
    env = _env()
    transfer = await _running(env)
    await _pause(env).execute(transfer.id, "offline")

    await _resume(env).execute(env.user_id, Platform.VK)  # VK — источник

    assert (await _stored(env, transfer.id)).status is TransferStatus.RUNNING


async def test_resume_ignores_other_users() -> None:
    env = _env()
    transfer = await _running(env)
    await _pause(env).execute(transfer.id, "offline")

    await _resume(env).execute(uuid4(), Platform.SPOTIFY)

    assert (await _stored(env, transfer.id)).status is TransferStatus.PAUSED_CLIENT


async def test_resume_writing_phase_requeues_write() -> None:
    env = _env()
    transfer = await _running(env, matched=1, pending=0)
    stored = await _stored(env, transfer.id)
    stored.status = TransferStatus.WRITING
    await _pause(env).execute(transfer.id, "timeout")

    await _resume(env).execute(env.user_id, Platform.SPOTIFY)

    assert stored.status is TransferStatus.WRITING
    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]


async def test_pause_ignored_for_finished_transfer() -> None:
    env = _env()
    transfer = await _running(env)
    (await _stored(env, transfer.id)).status = TransferStatus.DONE

    await _pause(env).execute(transfer.id, "offline")

    assert (await _stored(env, transfer.id)).status is TransferStatus.DONE


# --- задачи: расширение недоступно → пауза, а не повтор и не FAILED ---------------


class _RecordingClientPause:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    async def execute(self, transfer_id: UUID, reason: str) -> None:
        self.calls.append((transfer_id, reason))


class _UnavailableMatch:
    async def execute(self, transfer_id: UUID, position: int) -> None:
        raise ExtensionUnavailableError(Platform.SOUNDCLOUD, ExtensionUnavailableReason.OFFLINE)


async def test_match_task_pauses_for_client_without_retry() -> None:
    client_pause = _RecordingClientPause()
    transfer_id = uuid4()

    await match_with_retries(
        1,
        transfer_id,
        0,
        cast(MatchTransferItemUseCase, _UnavailableMatch()),
        cast(FailTransferItemUseCase, None),
        cast(PauseTransferForQuotaUseCase, RecordingPause()),
        pause_client=cast(PauseTransferForClientUseCase, client_pause),
    )

    assert client_pause.calls == [(transfer_id, "offline")]


async def test_write_task_pauses_for_client_instead_of_failing() -> None:
    client_pause = _RecordingClientPause()
    transfer_id = uuid4()

    async def execute(_: UUID) -> None:
        raise ExtensionUnavailableError(Platform.SOUNDCLOUD, ExtensionUnavailableReason.TIMEOUT)

    # Последняя попытка: без паузы ушло бы в FAILED (fail_transfer = None упал бы).
    await with_platform_retries(
        99,
        "run_write",
        transfer_id,
        execute,
        cast(FailTransferUseCase, None),
        cast(PauseTransferForQuotaUseCase, RecordingPause()),
        pause_client=cast(PauseTransferForClientUseCase, client_pause),
    )

    assert client_pause.calls == [(transfer_id, "timeout")]


# --- идемпотентность создания плейлиста ---------------------------------------------


async def test_write_passes_part_request_id_to_create_playlist() -> None:
    env = Env()
    target = FakeMusicPlatformGateway(platform=Platform.SPOTIFY, playlist_capacity=1)
    env.register_gateway(target)
    transfer = Transfer(
        id=uuid4(),
        user_id=env.user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=NewPlaylist(platform=Platform.SPOTIFY, title="Копия", description=None),
    )
    now = datetime.now(UTC)
    transfer.start(now)
    for position in range(2):
        transfer.add_item(position, ExternalTrackRef(Platform.VK, f"s{position}"))
        transfer.record_match(
            position,
            MatchResult(
                target_ref=ExternalTrackRef(Platform.SPOTIFY, f"t{position}"),
                method="isrc",
                score=MatchScore(1.0),
            ),
            now,
        )
    transfer.begin_writing(now)
    await env.seed_transfer(transfer)

    await WriteTransferUseCase(env.uow, env.transfers, env.gateway_factory, env.accounts).execute(
        transfer.id
    )

    # Ключ — перенос + номер части: повтор run_write даст те же ключи.
    assert target.create_request_ids == [f"{transfer.id}:1", f"{transfer.id}:2"]

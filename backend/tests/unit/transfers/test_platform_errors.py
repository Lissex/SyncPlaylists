"""Ошибки площадки в переносе: что терминально (FAILED с причиной), а что повторяется."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from arq.worker import Retry

from syncplaylists.modules.transfers.application.use_cases import (
    FailTransferItemUseCase,
    FailTransferUseCase,
    MatchTransferItemUseCase,
    WriteTransferUseCase,
)
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.events import TransferFailed
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    LibrarySource,
    MatchResult,
    NewPlaylist,
    PlaylistSource,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.modules.transfers.presentation.tasks import (
    MATCH_MAX_TRIES,
    PLATFORM_MAX_TRIES,
    RATE_LIMITED_MAX_TRIES,
    match_with_retries,
    with_platform_retries,
)
from syncplaylists.shared_kernel.domain.base import DomainEvent
from syncplaylists.shared_kernel.domain.errors import (
    PlatformAuthError,
    PlatformNotSupportedError,
    PlatformRateLimitedError,
    PlatformRegionError,
    PlatformUnavailableError,
    PlaylistNotFoundError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.value_objects import MatchScore, Platform, PlaylistRef
from tests.fakes import FakeMusicPlatformGateway, FakeUnitOfWork
from tests.unit.transfers.test_use_cases import _SPOTIFY_MATCH_1, _VK_TRACK_1, Env


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


def _failure_reasons(env: Env) -> list[str]:
    uow = cast(_EventsUnitOfWork, env.uow)
    return [e.reason for e in uow.events if isinstance(e, TransferFailed)]


async def _queued_transfer(env: Env) -> Transfer:
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    await env.seed_transfer(transfer)
    return transfer


async def _writing_transfer(env: Env, destination: ExistingPlaylist | NewPlaylist) -> Transfer:
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    now = datetime.now(UTC)
    transfer.start(now)
    transfer.add_item(0, _VK_TRACK_1.ref)
    transfer.record_match(
        0, MatchResult(target_ref=_SPOTIFY_MATCH_1.ref, method="fuzzy", score=MatchScore(0.95)), now
    )
    transfer.begin_writing(now)
    await env.seed_transfer(transfer)
    return transfer


def _write_use_case(env: Env) -> WriteTransferUseCase:
    return WriteTransferUseCase(env.uow, env.transfers, env.gateway_factory, env.accounts)


async def _status(env: Env, transfer_id: UUID) -> TransferStatus:
    stored = await env.transfers.get(transfer_id)
    assert stored is not None
    return stored.status


# --- 401: аккаунт EXPIRED только после перепроверки ---


async def test_auth_error_confirmed_fails_transfer_as_account_expired() -> None:
    env = _env()
    transfer = await _queued_transfer(env)
    gateway = FakeMusicPlatformGateway(platform=Platform.VK)
    gateway.failures["get_playlist"] = PlatformAuthError(Platform.VK)
    env.register_gateway(gateway)

    await env.process_transfer_use_case().execute(transfer.id)

    assert await _status(env, transfer.id) is TransferStatus.FAILED
    assert _failure_reasons(env) == ["account_expired"]
    assert env.accounts.auth_failures == [env.vk.account_id]
    assert env.task_queue.enqueued == []


async def test_auth_error_not_confirmed_is_retried_and_account_stays() -> None:
    env = _env()
    env.accounts.auth_failure_confirms = False
    transfer = await _queued_transfer(env)
    gateway = FakeMusicPlatformGateway(platform=Platform.VK)
    gateway.failures["get_playlist"] = PlatformAuthError(Platform.VK)
    env.register_gateway(gateway)

    with pytest.raises(PlatformAuthError):
        await env.process_transfer_use_case().execute(transfer.id)

    assert _failure_reasons(env) == []
    assert env.uow.commits == 0  # ничего не закоммичено — повтор начнёт заново
    assert env.task_queue.enqueued == []


async def test_match_auth_error_confirmed_fails_running_transfer() -> None:
    env = _env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    transfer.start(datetime.now(UTC))
    transfer.add_item(0, _VK_TRACK_1.ref)
    await env.seed_source_platform_track(_VK_TRACK_1)
    await env.seed_transfer(transfer)
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY)
    gateway.failures["search"] = PlatformAuthError(Platform.SPOTIFY)
    env.register_gateway(gateway)

    await env.match_transfer_item_use_case().execute(transfer.id, 0)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.FAILED
    assert stored.items[0].status is TransferItemStatus.PENDING
    assert _failure_reasons(env) == ["account_expired"]


# --- 403/404/гео: терминально, аккаунт не трогаем ---


async def test_write_to_foreign_playlist_fails_as_not_writable_without_expiring() -> None:
    env = _env()
    transfer = await _writing_transfer(
        env, ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    )
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY)
    gateway.failures["add_tracks"] = PlaylistNotWritableError(Platform.SPOTIFY)
    env.register_gateway(gateway)

    await _write_use_case(env).execute(transfer.id)

    assert await _status(env, transfer.id) is TransferStatus.FAILED
    assert _failure_reasons(env) == ["playlist_not_writable"]
    assert env.accounts.auth_failures == []


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PlaylistNotFoundError(Platform.VK), "playlist_not_found"),
        (PlatformRegionError(Platform.VK), "region_blocked"),
    ],
)
async def test_source_terminal_errors_fail_transfer(error: Exception, reason: str) -> None:
    env = _env()
    transfer = await _queued_transfer(env)
    gateway = FakeMusicPlatformGateway(platform=Platform.VK)
    gateway.failures["get_playlist"] = error
    env.register_gateway(gateway)

    await env.process_transfer_use_case().execute(transfer.id)

    assert _failure_reasons(env) == [reason]


async def test_unsupported_source_platform_fails_transfer() -> None:
    env = _env()
    transfer = await _queued_transfer(env)
    env.gateway_factory.unsupported.add(Platform.VK)

    await env.process_transfer_use_case().execute(transfer.id)

    assert _failure_reasons(env) == ["platform_not_supported"]


# --- временные ошибки: проброс в retry ---


@pytest.mark.parametrize(
    "error",
    [PlatformUnavailableError(Platform.SPOTIFY), PlatformRateLimitedError(Platform.SPOTIFY, 3)],
)
async def test_transient_write_errors_propagate_for_retry(error: Exception) -> None:
    env = _env()
    transfer = await _writing_transfer(
        env, ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    )
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY)
    gateway.failures["add_tracks"] = error
    env.register_gateway(gateway)

    with pytest.raises(type(error)):
        await _write_use_case(env).execute(transfer.id)

    assert await _status(env, transfer.id) is TransferStatus.WRITING


class _FailingExecute:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def __call__(self, transfer_id: UUID) -> None:
        raise self._error


class _RecordingFailTransfer:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    async def execute(self, transfer_id: UUID, reason: str) -> None:
        self.calls.append((transfer_id, reason))


async def test_platform_retries_defer_at_least_retry_after() -> None:
    fail = _RecordingFailTransfer()

    with pytest.raises(Retry) as caught:
        await with_platform_retries(
            1,
            "run_write",
            uuid4(),
            _FailingExecute(PlatformRateLimitedError(Platform.YANDEX, 42)),
            cast(FailTransferUseCase, fail),
        )

    assert caught.value.defer_score is not None
    assert caught.value.defer_score >= 42_000  # Retry хранит задержку в мс
    assert fail.calls == []


async def test_platform_retries_fail_transfer_after_last_try() -> None:
    fail = _RecordingFailTransfer()
    transfer_id = uuid4()

    await with_platform_retries(
        PLATFORM_MAX_TRIES,
        "run_transfer",
        transfer_id,
        _FailingExecute(PlatformUnavailableError(Platform.YANDEX)),
        cast(FailTransferUseCase, fail),
    )

    assert fail.calls == [(transfer_id, "platform_unavailable")]


async def test_platform_retries_do_not_swallow_bugs() -> None:
    with pytest.raises(ZeroDivisionError):
        await with_platform_retries(
            1,
            "run_write",
            uuid4(),
            _FailingExecute(ZeroDivisionError()),
            cast(FailTransferUseCase, _RecordingFailTransfer()),
        )


async def test_fail_transfer_use_case_fails_queued_transfer() -> None:
    env = _env()
    transfer = await _queued_transfer(env)

    await FailTransferUseCase(env.uow, env.transfers).execute(transfer.id, "platform_unavailable")

    assert await _status(env, transfer.id) is TransferStatus.FAILED
    assert _failure_reasons(env) == ["platform_unavailable"]


async def test_fail_transfer_use_case_ignores_finished_transfer() -> None:
    env = _env()
    transfer = await _queued_transfer(env)
    use_case = FailTransferUseCase(env.uow, env.transfers)
    await use_case.execute(transfer.id, "first")

    await use_case.execute(transfer.id, "second")

    assert _failure_reasons(env) == ["first"]


# --- StartTransfer: право записи в существующий плейлист ---


async def test_start_transfer_rejects_foreign_existing_playlist() -> None:
    env = _env()
    env.register_gateway(
        FakeMusicPlatformGateway(platform=Platform.SPOTIFY, playlist_owner="someone-else")
    )
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "their-playlist"))

    with pytest.raises(PlaylistNotWritableError):
        await env.start_transfer_use_case().execute(env.user_id, source, destination)

    assert env.task_queue.enqueued == []
    assert env.transfers.save_calls == 0


async def test_start_transfer_to_own_existing_playlist_is_accepted() -> None:
    env = _env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "my-playlist"))

    dto = await env.start_transfer_use_case().execute(env.user_id, source, destination)

    assert dto.status == "queued"


async def test_start_transfer_rejects_unsupported_platform() -> None:
    env = _env()
    env.gateway_factory.unsupported.add(Platform.VK)
    source = LibrarySource(platform=Platform.VK, account_id=env.vk.account_id)
    destination = NewPlaylist(platform=Platform.SPOTIFY, title="x", description=None)

    with pytest.raises(PlatformNotSupportedError):
        await env.start_transfer_use_case().execute(env.user_id, source, destination)


# --- rate limit: отдельный бюджет повторов -------------------------------------------


class _FailingMatch:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def execute(self, transfer_id: UUID, position: int) -> None:
        raise self._error


class _RecordingFailItem:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int, str]] = []

    async def execute(self, transfer_id: UUID, position: int, reason: str) -> None:
        self.calls.append((transfer_id, position, reason))


@pytest.mark.parametrize("job_try", [MATCH_MAX_TRIES, RATE_LIMITED_MAX_TRIES - 1])
async def test_rate_limited_match_is_retried_beyond_normal_budget(job_try: int) -> None:
    fail_item = _RecordingFailItem()

    with pytest.raises(Retry) as caught:
        await match_with_retries(
            job_try,
            uuid4(),
            0,
            cast(
                MatchTransferItemUseCase,
                _FailingMatch(PlatformRateLimitedError(Platform.YANDEX, 600)),
            ),
            cast(FailTransferItemUseCase, fail_item),
        )

    assert fail_item.calls == []
    # Не раньше Retry-After площадки и с разбросом не больше 20%.
    assert caught.value.defer_score is not None
    assert 600_000 <= caught.value.defer_score <= 720_000


async def test_rate_limited_match_fails_item_after_its_own_budget() -> None:
    fail_item = _RecordingFailItem()
    transfer_id = uuid4()

    await match_with_retries(
        RATE_LIMITED_MAX_TRIES,
        transfer_id,
        7,
        cast(MatchTransferItemUseCase, _FailingMatch(PlatformRateLimitedError(Platform.YANDEX, 5))),
        cast(FailTransferItemUseCase, fail_item),
    )

    assert fail_item.calls == [(transfer_id, 7, "PlatformRateLimitedError")]


async def test_other_errors_keep_normal_budget() -> None:
    fail_item = _RecordingFailItem()

    await match_with_retries(
        MATCH_MAX_TRIES,
        uuid4(),
        0,
        cast(MatchTransferItemUseCase, _FailingMatch(PlatformUnavailableError(Platform.YANDEX))),
        cast(FailTransferItemUseCase, fail_item),
    )

    assert len(fail_item.calls) == 1


async def test_rate_limited_transfer_is_retried_beyond_normal_budget() -> None:
    fail = _RecordingFailTransfer()

    with pytest.raises(Retry):
        await with_platform_retries(
            PLATFORM_MAX_TRIES,
            "run_transfer",
            uuid4(),
            _FailingExecute(PlatformRateLimitedError(Platform.YANDEX, 30)),
            cast(FailTransferUseCase, fail),
        )
    assert fail.calls == []


# --- новый плейлист: create — отдельный шаг с немедленным коммитом ------------------


async def test_new_playlist_is_committed_before_tracks_are_written() -> None:
    env = _env()
    transfer = await _writing_transfer(
        env, NewPlaylist(platform=Platform.SPOTIFY, title="Копия", description=None)
    )
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY)
    gateway.failures["add_tracks"] = PlatformUnavailableError(Platform.SPOTIFY)
    env.register_gateway(gateway)

    with pytest.raises(PlatformUnavailableError):
        await _write_use_case(env).execute(transfer.id)

    # Плейлист создан и ссылка на него закоммичена ДО упавшей записи треков —
    # откат второй транзакции её не потеряет (на Postgres: test_write_idempotency).
    assert gateway.created_playlists == [("Копия", None)]
    assert env.uow.commits == 1
    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.resolved_target == PlaylistRef(Platform.SPOTIFY, "created-1")

    del gateway.failures["add_tracks"]
    await _write_use_case(env).execute(transfer.id)  # повтор run_write

    assert gateway.created_playlists == [("Копия", None)]  # второй не создан
    assert await _status(env, transfer.id) is TransferStatus.DONE

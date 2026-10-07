"""Переносы через браузерное расширение: задачи — в своей очереди (TaskLane.CLIENT), запись
— пачками, одна задача run_write на пачку (этап 4c-2). Только фейки."""

from datetime import UTC, datetime
from uuid import uuid4

from syncplaylists.modules.transfers.application.use_cases import (
    ResumeClientPausedTransfersUseCase,
    WriteTransferUseCase,
)
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    LibraryDestination,
    MatchResult,
    NewPlaylist,
    PlaylistSource,
    TrackDestination,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.shared_kernel.application.ports import TaskLane
from syncplaylists.shared_kernel.domain.errors import ExtensionUnavailableReason
from syncplaylists.shared_kernel.domain.search import InsertOrder, PlaylistSnapshot
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
    Transport,
)
from tests.fakes import FakeMusicPlatformGateway
from tests.unit.transfers.test_use_cases import _VK_TRACK_1, Env


def _spotify_via_extension(env: Env) -> None:
    env.accounts.disconnect(env.spotify.account_id)
    env.spotify = env.accounts.connect(env.user_id, Platform.SPOTIFY, Transport.EXTENSION)


async def test_start_via_extension_marks_transfer_and_uses_client_lane() -> None:
    env = Env()
    _spotify_via_extension(env)

    dto = await env.start_transfer_use_case().execute(
        env.user_id,
        PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
    )

    stored = await env.transfers.get(dto.id)
    assert stored is not None
    assert stored.via_client
    assert env.task_queue.lanes == [("run_transfer", TaskLane.CLIENT)]


async def test_start_without_extension_uses_default_lane() -> None:
    env = Env()

    dto = await env.start_transfer_use_case().execute(
        env.user_id,
        PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
    )

    stored = await env.transfers.get(dto.id)
    assert stored is not None
    assert not stored.via_client
    assert env.task_queue.lanes == [("run_transfer", TaskLane.DEFAULT)]


async def test_process_via_client_enqueues_matches_in_client_lane() -> None:
    env = Env()
    env.register_gateway(
        FakeMusicPlatformGateway(
            platform=Platform.VK,
            playlist=PlaylistSnapshot(
                ref=PlaylistRef(Platform.VK, "src"),
                title="src",
                description=None,
                tracks=(_VK_TRACK_1,),
            ),
        )
    )
    transfer = Transfer(
        id=uuid4(),
        user_id=env.user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
        via_client=True,
    )
    await env.seed_transfer(transfer)

    await env.process_transfer_use_case().execute(transfer.id)

    assert env.task_queue.lanes == [("run_match", TaskLane.CLIENT)]


async def test_resume_after_client_pause_keeps_client_lane() -> None:
    env = Env()
    transfer = Transfer(
        id=uuid4(),
        user_id=env.user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
        via_client=True,
    )
    await env.seed_transfer(transfer)
    await env.transfers.pause_for_client(transfer.id, ExtensionUnavailableReason.OFFLINE.value)

    await ResumeClientPausedTransfersUseCase(env.uow, env.transfers, env.task_queue).execute(
        env.user_id, Platform.SPOTIFY
    )

    assert env.task_queue.lanes == [("run_transfer", TaskLane.CLIENT)]


def _writing(env: Env, destination: TrackDestination, count: int, *, via_client: bool) -> Transfer:
    transfer = Transfer(
        id=uuid4(),
        user_id=env.user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=destination,
        via_client=via_client,
    )
    now = datetime.now(UTC)
    transfer.start(now)
    for position in range(count):
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
    return transfer


def _refs(*positions: int) -> list[ExternalTrackRef]:
    return [ExternalTrackRef(Platform.SPOTIFY, f"t{p}") for p in positions]


async def _write_all(env: Env, transfer: Transfer, batch: int) -> int:
    """Выполняет run_write, пока задачи ставятся заново (как воркер). Возвращает число задач."""
    use_case = WriteTransferUseCase(
        env.uow,
        env.transfers,
        env.gateway_factory,
        env.accounts,
        env.task_queue,
        client_batch_size=batch,
    )
    jobs = 0
    while True:
        jobs += 1
        before = len(env.task_queue.lanes)
        await use_case.execute(transfer.id)
        queued = env.task_queue.lanes[before:]
        if not queued:
            return jobs
        assert queued == [("run_write", TaskLane.CLIENT)]


async def test_client_write_goes_in_batches_one_job_each() -> None:
    env = Env()
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY)
    env.register_gateway(gateway)
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst"))
    transfer = _writing(env, destination, 5, via_client=True)
    await env.seed_transfer(transfer)

    use_case = WriteTransferUseCase(
        env.uow,
        env.transfers,
        env.gateway_factory,
        env.accounts,
        env.task_queue,
        client_batch_size=2,
    )
    await use_case.execute(transfer.id)

    # Первая пачка записана и отмечена, перенос ещё пишется, следующая пачка — в очереди.
    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.WRITING
    assert [i.status for i in stored.items] == [
        TransferItemStatus.ADDED,
        TransferItemStatus.ADDED,
        TransferItemStatus.MATCHED,
        TransferItemStatus.MATCHED,
        TransferItemStatus.MATCHED,
    ]
    assert gateway.added_to_playlist == _refs(0, 1)
    assert env.task_queue.lanes == [("run_write", TaskLane.CLIENT)]

    assert await _write_all(env, transfer, batch=2) == 2
    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.DONE
    assert gateway.added_to_playlist == _refs(0, 1, 2, 3, 4)


async def test_server_write_is_not_batched() -> None:
    env = Env()
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY)
    env.register_gateway(gateway)
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst"))
    transfer = _writing(env, destination, 5, via_client=False)
    await env.seed_transfer(transfer)

    assert await _write_all(env, transfer, batch=2) == 1
    assert gateway.added_to_playlist == _refs(0, 1, 2, 3, 4)


async def test_batches_keep_split_playlist_parts_stable() -> None:
    env = Env()
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY, playlist_capacity=3)
    env.register_gateway(gateway)
    transfer = _writing(env, NewPlaylist(platform=Platform.SPOTIFY, title="L"), 5, via_client=True)
    await env.seed_transfer(transfer)

    await _write_all(env, transfer, batch=2)

    first, second = (
        PlaylistRef(Platform.SPOTIFY, "created-1"),
        PlaylistRef(Platform.SPOTIFY, "created-2"),
    )
    assert gateway.created_playlists == [("L (1/2)", None), ("L (2/2)", None)]
    assert gateway.playlist_contents == {first: _refs(0, 1, 2), second: _refs(3, 4)}


async def test_batches_keep_reversed_order_for_top_insert_library() -> None:
    env = Env()
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY, insert_order=InsertOrder.TOP)
    env.register_gateway(gateway)
    destination = LibraryDestination(platform=Platform.SPOTIFY, account_id=env.spotify.account_id)
    transfer = _writing(env, destination, 5, via_client=True)
    await env.seed_transfer(transfer)

    assert await _write_all(env, transfer, batch=2) == 3
    # Как без пачек: самый свежий трек источника (позиция 0) добавлен последним — сверху.
    assert gateway.added_to_library == _refs(4, 3, 2, 1, 0)

"""NewPlaylist больше вместимости плейлиста площадки (SoundCloud — 500): несколько
плейлистов «<название> (k/N)», каждый создаётся отдельным шагом с коммитом — повтор
run_write переиспользует уже созданные (ARCHITECTURE.md, 11e)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.transfers.application.use_cases import WriteTransferUseCase
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    MatchResult,
    NewPlaylist,
    PlaylistSource,
    TrackDestination,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.errors import PlatformUnavailableError
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)
from tests.fakes import FakeMusicPlatformGateway
from tests.unit.transfers.test_use_cases import Env


def _target(n: int) -> ExternalTrackRef:
    return ExternalTrackRef(Platform.SPOTIFY, f"t{n}")


async def _writing_transfer(
    env: Env, destination: TrackDestination, targets: list[int]
) -> Transfer:
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    now = datetime.now(UTC)
    transfer.start(now)
    for position, target in enumerate(targets):
        transfer.add_item(position, ExternalTrackRef(Platform.VK, f"s{position}"))
        result = MatchResult(target_ref=_target(target), method="fuzzy", score=MatchScore(0.95))
        transfer.record_match(position, result, now)
    transfer.begin_writing(now)
    await env.seed_transfer(transfer)
    return transfer


def _use_case(env: Env) -> WriteTransferUseCase:
    return WriteTransferUseCase(
        env.uow, env.transfers, env.gateway_factory, env.accounts, env.task_queue
    )


_NEW = NewPlaylist(platform=Platform.SPOTIFY, title="Лайки", description="из VK")


async def test_new_playlist_over_capacity_is_split_into_parts() -> None:
    env = Env()
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY, playlist_capacity=2)
    env.register_gateway(gateway)
    transfer = await _writing_transfer(env, _NEW, [1, 2, 3, 4, 5])

    await _use_case(env).execute(transfer.id)

    assert gateway.created_playlists == [
        ("Лайки (1/3)", "из VK"),
        ("Лайки (2/3)", "из VK"),
        ("Лайки (3/3)", "из VK"),
    ]
    parts = [PlaylistRef(Platform.SPOTIFY, f"created-{k}") for k in (1, 2, 3)]
    assert [gateway.playlist_contents[p] for p in parts] == [
        [_target(1), _target(2)],
        [_target(3), _target(4)],
        [_target(5)],
    ]
    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.resolved_targets == tuple(parts)
    assert stored.status is TransferStatus.DONE
    assert {item.status for item in stored.items} == {TransferItemStatus.ADDED}


async def test_duplicates_do_not_take_capacity() -> None:
    env = Env()
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY, playlist_capacity=2)
    env.register_gateway(gateway)
    # Два трека источника нашлись как один и тот же трек назначения.
    transfer = await _writing_transfer(env, _NEW, [1, 1, 2])

    await _use_case(env).execute(transfer.id)

    assert gateway.created_playlists == [("Лайки", "из VK")]


async def test_retry_reuses_parts_created_before_failure() -> None:
    env = Env()
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY, playlist_capacity=2)
    env.register_gateway(gateway)
    transfer = await _writing_transfer(env, _NEW, [1, 2, 3])
    gateway.failures["add_tracks"] = PlatformUnavailableError(Platform.SPOTIFY)

    with pytest.raises(PlatformUnavailableError):
        await _use_case(env).execute(transfer.id)
    assert len(gateway.created_playlists) == 2
    assert env.uow.commits == 2  # каждый плейлист закоммичен сразу после создания

    del gateway.failures["add_tracks"]
    await _use_case(env).execute(transfer.id)

    assert len(gateway.created_playlists) == 2
    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.DONE


async def test_existing_playlist_is_not_split() -> None:
    env = Env()
    gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY, playlist_capacity=1)
    env.register_gateway(gateway)
    dst = PlaylistRef(Platform.SPOTIFY, "dst")
    transfer = await _writing_transfer(env, ExistingPlaylist(ref=dst), [1, 2])

    await _use_case(env).execute(transfer.id)

    # Остаток сверх лимита — забота адаптера (AddResult.failed), плейлисты не создаются.
    assert gateway.created_playlists == []
    assert gateway.playlist_contents[dst] == [_target(1), _target(2)]


def test_same_target_cannot_be_added_twice() -> None:
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src"))
    transfer = Transfer(id=uuid4(), user_id=uuid4(), source=source, destination=_NEW)
    ref = PlaylistRef(Platform.SPOTIFY, "p")
    transfer.add_resolved_target(ref)
    with pytest.raises(InvalidTransferTransitionError):
        transfer.add_resolved_target(ref)
    assert transfer.resolved_target == ref

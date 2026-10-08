"""Площадка, подключённая транспортом только для чтения (SoundCloud по токену: запись
закрыта антиботом), назначением быть не может: явный отказ при старте и FAILED при
записи, а не повторы до platform_unavailable (ARCHITECTURE.md, 11e)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.transfers.application.use_cases import WriteTransferUseCase
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    MatchResult,
    NewPlaylist,
    PlaylistSource,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.errors import WriteRequiresExtensionError
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
    Transport,
)
from tests.fakes import FakeMusicPlatformGateway
from tests.unit.transfers.test_platform_errors import _env as _events_env
from tests.unit.transfers.test_platform_errors import _failure_reasons
from tests.unit.transfers.test_use_cases import Env

_SOURCE = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
_DESTINATION = NewPlaylist(platform=Platform.SOUNDCLOUD, title="Перенос")


def _env(transport: Transport) -> Env:
    env = _events_env()
    env.accounts.connect(env.user_id, Platform.SOUNDCLOUD, transport=transport)
    env.gateway_factory.read_only.add((Platform.SOUNDCLOUD, Transport.UNOFFICIAL))
    return env


async def test_start_refuses_destination_connected_read_only() -> None:
    env = _env(Transport.UNOFFICIAL)

    with pytest.raises(WriteRequiresExtensionError):
        await env.start_transfer_use_case().execute(env.user_id, _SOURCE, _DESTINATION)

    assert env.task_queue.enqueued == []
    assert env.transfers.save_calls == 0


async def test_start_accepts_same_platform_via_extension() -> None:
    env = _env(Transport.EXTENSION)

    dto = await env.start_transfer_use_case().execute(env.user_id, _SOURCE, _DESTINATION)

    assert dto.status == "queued"


async def test_write_fails_at_once_when_destination_became_read_only() -> None:
    # Перенос начинали через расширение, а посреди записи площадку переподключили токеном.
    env = _env(Transport.UNOFFICIAL)
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=_SOURCE, destination=_DESTINATION)
    now = datetime.now(UTC)
    transfer.start(now)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_match(
        0,
        MatchResult(
            target_ref=ExternalTrackRef(Platform.SOUNDCLOUD, "1"),
            method="fuzzy",
            score=MatchScore(0.95),
        ),
        now,
    )
    transfer.begin_writing(now)
    await env.seed_transfer(transfer)
    target = FakeMusicPlatformGateway(platform=Platform.SOUNDCLOUD)
    env.register_gateway(target)

    await WriteTransferUseCase(
        env.uow, env.transfers, env.gateway_factory, env.accounts, env.task_queue
    ).execute(transfer.id)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.FAILED
    assert _failure_reasons(env) == ["write_requires_extension"]
    assert target.created_playlists == []


def test_api_answers_422_write_requires_extension() -> None:
    from syncplaylists.modules.transfers.presentation.api import _link_or_platform_error

    error = _link_or_platform_error(WriteRequiresExtensionError(Platform.SOUNDCLOUD))

    assert error.status_code == 422
    assert isinstance(error.detail, dict)
    assert error.detail["code"] == "write_requires_extension"


async def test_client_paused_platforms_lists_source_and_destination() -> None:
    # Для pong расширению: на каких площадках переносы ждут браузер.
    from syncplaylists.modules.transfers.application.use_cases import (
        ClientPausedPlatformsUseCase,
    )

    env = _env(Transport.EXTENSION)
    paused = Transfer(id=uuid4(), user_id=env.user_id, source=_SOURCE, destination=_DESTINATION)
    paused.status = TransferStatus.PAUSED_CLIENT
    running = Transfer(
        id=uuid4(),
        user_id=env.user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.YANDEX, "p")),
        destination=NewPlaylist(platform=Platform.SPOTIFY, title="x"),
    )
    foreign = Transfer(id=uuid4(), user_id=uuid4(), source=_SOURCE, destination=_DESTINATION)
    foreign.status = TransferStatus.PAUSED_CLIENT
    for transfer in (paused, running, foreign):
        await env.seed_transfer(transfer)

    platforms = await ClientPausedPlatformsUseCase(env.transfers).for_user(env.user_id)

    assert platforms == {Platform.VK, Platform.SOUNDCLOUD}

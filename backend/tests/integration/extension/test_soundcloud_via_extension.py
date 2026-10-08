"""Сквозной SoundCloud через расширение (4c-3) на настоящем Redis (testcontainers):
SoundCloudGateway → ExtensionSoundCloudApi → RedisExtensionChannel → фейковое расширение
(FakeSoundCloudBrowser) и обратно. «Браузер закрыли» сразу после создания сета →
повтор с тем же request_id → на площадке один сет, треки без дублей; задача для другого
аккаунта площадки → session_mismatch."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis

from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.factory import (
    SoundCloudApiFactory,
    SoundCloudExtensionGatewayBuilder,
    SoundCloudLimits,
)
from syncplaylists.integrations.platforms.soundcloud.gateway import SoundCloudGateway
from syncplaylists.integrations.platforms.soundcloud.tokens import TokenEndpoint
from syncplaylists.modules.extension.application.operations import OPERATIONS, OperationSpec
from syncplaylists.modules.extension.infrastructure.redis_channel import RedisExtensionChannel
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    ExtensionUnavailableReason,
)
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    Platform,
    Transport,
)
from tests.tools.fake_extension import FakeSoundCloudBrowser, HubDevice

_UID = "900001"
_GatewayFor = Callable[[UUID], SoundCloudGateway]
_FAST = {
    name: OperationSpec(timeout_seconds=3, write=spec.write) for name, spec in OPERATIONS.items()
}


class _NoCache:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        return None


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def channel(redis: Redis) -> RedisExtensionChannel:
    return RedisExtensionChannel(redis, presence_ttl_seconds=60, operations=_FAST)


@pytest.fixture
async def gateway_for(channel: RedisExtensionChannel) -> AsyncIterator[_GatewayFor]:
    # Публичное в этих сценариях не читается — HTTP-клиент сервера в сеть не ходит.
    async with httpx.AsyncClient() as http:
        apis = SoundCloudApiFactory(
            http,
            ClientIdProvider(
                http, _NoCache(), ttl_seconds=60, min_refresh_seconds=60, timeout_seconds=1
            ),
            TokenEndpoint(http, timeout_seconds=1),
            timeout_seconds=1,
        )
        builder = SoundCloudExtensionGatewayBuilder(apis, channel, SoundCloudLimits())

        def build(user_id: UUID) -> SoundCloudGateway:
            access = AccountAccess(
                account_id=uuid4(),
                user_id=user_id,
                platform=Platform.SOUNDCLOUD,
                transport=Transport.EXTENSION,
                external_user_id=_UID,
                credentials=None,
            )
            return builder(access)

        yield build


def _device(channel: RedisExtensionChannel, user_id: UUID, uid: str = _UID) -> HubDevice:
    return HubDevice(
        hub=channel,
        user_id=user_id,
        device_id=uuid4(),
        platform=FakeSoundCloudBrowser(external_user_id=uid),
    )


@contextlib.asynccontextmanager
async def _serving(device: HubDevice, **kwargs: bool) -> AsyncIterator[None]:
    task = asyncio.create_task(device.serve(**kwargs))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _refs(*ids: int) -> list[ExternalTrackRef]:
    return [ExternalTrackRef(Platform.SOUNDCLOUD, str(i)) for i in ids]


async def test_browser_closed_after_create_then_one_set_without_duplicates(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    browser = device.platform
    assert isinstance(browser, FakeSoundCloudBrowser)
    gateway = gateway_for(user_id)
    device.lose_result_of.add("create_playlist")
    await device.go_online()

    # 1) Сет создан в браузере, ответ до сервера не дошёл — браузер закрыли.
    async with _serving(device, stop_after_loss=True):
        with pytest.raises(ExtensionUnavailableError) as raised:
            await gateway.create_playlist("Лайки Яндекса", None, request_id="t-1:1")
    assert raised.value.reason is ExtensionUnavailableReason.OFFLINE
    assert len(browser.sets) == 1

    # 2) Браузер открыли: повтор с тем же request_id не создаёт второй сет (журнал).
    device.lost.clear()
    await device.go_online()
    async with _serving(device):
        ref = await gateway.create_playlist("Лайки Яндекса", None, request_id="t-1:1")
        first = await gateway.add_tracks(ref, _refs(11, 12))
        # Повтор пачки после сбоя (часть уже в сете) — без дублей.
        second = await gateway.add_tracks(ref, _refs(12, 13))

    assert len(browser.sets) == 1
    assert browser.executed.count("create_playlist") == 1
    [(set_id, tracks)] = browser.sets.items()
    assert ref.external_id == f"{set_id}:s-{set_id}"
    assert tracks == [11, 12, 13]
    assert not first.failed
    assert not second.failed


async def test_likes_skip_already_liked(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    browser = device.platform
    assert isinstance(browser, FakeSoundCloudBrowser)
    browser.likes = [2]
    gateway = gateway_for(user_id)
    await device.go_online()

    async with _serving(device):
        result = await gateway.add_to_library(_refs(1, 2, 3))
        library = [t.ref.external_id async for t in gateway.get_library()]

    assert set(result.added) == set(_refs(1, 2, 3))
    assert browser.executed.count("like") == 2
    assert library == ["3", "1", "2"]


async def test_other_account_in_browser_is_session_mismatch(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor
) -> None:
    user_id = uuid4()
    # Присутствие сообщило нужный аккаунт, а к моменту задачи в браузере уже другой:
    # расширение сверяет account задачи и отказывается.
    device = _device(channel, user_id)
    await device.go_online()
    browser = device.platform
    browser.external_user_id = "900002"
    gateway = gateway_for(user_id)

    async with _serving(device):
        with pytest.raises(ExtensionUnavailableError) as raised:
            await gateway.create_playlist("Сет", None, request_id="t-2:1")

    assert raised.value.reason is ExtensionUnavailableReason.SESSION_MISMATCH
    assert isinstance(browser, FakeSoundCloudBrowser)
    assert browser.sets == {}

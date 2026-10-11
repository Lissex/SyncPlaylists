"""Сквозной VK через расширение (4c-5) на настоящем Redis (testcontainers):
VkGateway → ExtensionVkApi → RedisExtensionChannel → фейковое расширение (FakeVkBrowser) и
обратно.

- «Браузер закрыли» после создания плейлиста → повтор с тем же request_id → один плейлист;
- ответ записи пачки потерян → повтор → треки без дублей (копии узнаются по оригиналу);
- «Моя музыка»: повтор не задваивает, новое сверху;
- другой аккаунт в браузере → session_mismatch; капча → PAUSED_CLIENT(captcha), а не пауза
  по квоте, и серверных ключей ratelimit:* нет."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from syncplaylists.infrastructure.ratelimit.redis_write_guard import RedisWriteGuard
from syncplaylists.integrations.platforms.vk.factory import VkExtensionGatewayBuilder
from syncplaylists.integrations.platforms.vk.gateway import VkGateway, VkLimits
from syncplaylists.modules.extension.application.operations import OPERATIONS, OperationSpec
from syncplaylists.modules.extension.infrastructure.redis_channel import RedisExtensionChannel
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    ExtensionUnavailableReason,
)
from syncplaylists.shared_kernel.domain.search import TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    Platform,
    Transport,
)
from tests.tools.fake_extension import FakeVkBrowser, HubDevice

_ME = "100500"
_GatewayFor = Callable[[UUID], VkGateway]
_FAST = {
    name: OperationSpec(timeout_seconds=3, write=spec.write) for name, spec in OPERATIONS.items()
}


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
def gateway_for(channel: RedisExtensionChannel, redis: Redis) -> _GatewayFor:
    builder = VkExtensionGatewayBuilder(channel, RedisWriteGuard(redis), VkLimits(), page_size=2)

    def build(user_id: UUID) -> VkGateway:
        return builder(
            AccountAccess(
                account_id=uuid4(),
                user_id=user_id,
                platform=Platform.VK,
                transport=Transport.EXTENSION,
                external_user_id=_ME,
                credentials=None,
            )
        )

    return build


def _device(channel: RedisExtensionChannel, user_id: UUID) -> HubDevice:
    return HubDevice(hub=channel, user_id=user_id, device_id=uuid4(), platform=FakeVkBrowser())


def _browser(device: HubDevice) -> FakeVkBrowser:
    assert isinstance(device.platform, FakeVkBrowser)
    return device.platform


@contextlib.asynccontextmanager
async def _serving(device: HubDevice, **kwargs: bool) -> AsyncIterator[None]:
    task = asyncio.create_task(device.serve(**kwargs))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _refs(*ids: str) -> list[ExternalTrackRef]:
    return [ExternalTrackRef(Platform.VK, i) for i in ids]


def _originals(browser: FakeVkBrowser, plains: list[str]) -> list[str]:
    return [str(browser.catalog[p]["release_audio_id"]) for p in plains]


async def test_browser_closed_mid_write_then_one_playlist_without_duplicates(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    browser = _browser(device)
    gateway = gateway_for(user_id)
    device.lose_result_of.add("create_playlist")
    await device.go_online()

    # 1) Плейлист создан в браузере, ответ потерян — браузер закрыли.
    async with _serving(device, stop_after_loss=True):
        with pytest.raises(ExtensionUnavailableError) as raised:
            await gateway.create_playlist("Лайки SoundCloud", None, request_id="t-1:1")
    assert raised.value.reason is ExtensionUnavailableReason.OFFLINE
    assert len(browser.lists) == 1

    # 2) Браузер открыли: тот же request_id не создаёт второй плейлист; пачка треков
    # записана, а ответ снова потерян.
    device.lost.clear()
    device.lose_result_of.add("set_playlist_tracks")
    await device.go_online()
    async with _serving(device, stop_after_loss=True):
        ref = await gateway.create_playlist("Лайки SoundCloud", None, request_id="t-1:1")
        with pytest.raises(ExtensionUnavailableError):
            await gateway.add_tracks(ref, _refs("-2001_1", "-2001_2"))

    # 3) Повтор всей пачки (как run_write после паузы) + новый трек — без дублей.
    device.lost.clear()
    await device.go_online()
    async with _serving(device):
        result = await gateway.add_tracks(ref, _refs("-2001_1", "-2001_2", "-2001_3"))

    [(playlist_id, tracks)] = browser.lists.items()
    assert ref.external_id == f"{_ME}_{playlist_id}_k{playlist_id}"
    assert browser.executed.count("create_playlist") == 1
    assert _originals(browser, tracks) == ["-2001_1", "-2001_2", "-2001_3"]
    assert set(result.added) == set(_refs("-2001_1", "-2001_2", "-2001_3"))
    assert not result.failed


async def test_library_paged_repeat_does_not_duplicate(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    browser = _browser(device)
    gateway = gateway_for(user_id)
    await device.go_online()

    async with _serving(device):
        await gateway.add_to_library(_refs("-2001_1", "-2001_2", "-2001_3"))
        # Повтор той же пачки (run_write после сбоя): всё уже в «Моей музыке».
        again = await gateway.add_to_library(_refs("-2001_1", "-2001_2", "-2001_3"))
        library = [t.ref.external_id async for t in gateway.get_library()]

    assert set(again.added) == set(_refs("-2001_1", "-2001_2", "-2001_3"))
    assert browser.executed.count("add_to_library") == 3
    # Последний трек списка — на самом верху «Моей музыки».
    assert _originals(browser, browser.saved) == ["-2001_3", "-2001_2", "-2001_1"]
    assert len(library) == 3  # page_size=2 → две страницы


async def test_other_account_in_browser_is_session_mismatch(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    await device.go_online()
    browser = _browser(device)
    browser.external_user_id = "200600"
    gateway = gateway_for(user_id)

    async with _serving(device):
        with pytest.raises(ExtensionUnavailableError) as raised:
            await gateway.create_playlist("Плейлист", None, request_id="t-2:1")

    assert raised.value.reason is ExtensionUnavailableReason.SESSION_MISMATCH
    assert browser.lists == {}


async def test_captcha_is_client_pause_without_server_limits(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor, redis: Redis
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    _browser(device).captcha = True
    gateway = gateway_for(user_id)
    await device.go_online()

    async with _serving(device):
        with pytest.raises(ExtensionUnavailableError) as raised:
            await gateway.search(TrackQuery(title="Call Back", artist="Don Toliver"))

    assert raised.value.reason is ExtensionUnavailableReason.CAPTCHA
    assert [key async for key in redis.scan_iter("ratelimit:*")] == []

"""Сквозной Яндекс через расширение (4c-4) на настоящем Redis (testcontainers):
YandexGateway → ExtensionYandexClient → RedisExtensionChannel → фейковое расширение
(FakeYandexBrowser) и обратно.

- «Браузер закрыли» после создания плейлиста → повтор с тем же request_id → один плейлист;
- ответ пачки треков потерян → повтор → треки без дублей (ревизия + отсев в шлюзе);
- другой аккаунт в браузере → session_mismatch; капча → PAUSED_CLIENT(captcha), а не пауза
  по квоте, и общий серверный bucket Яндекса не тронут."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from syncplaylists.integrations.platforms.yandex.factory import YandexExtensionGatewayBuilder
from syncplaylists.integrations.platforms.yandex.gateway import YandexGateway
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
from tests.tools.fake_extension import FakeYandexBrowser, HubDevice

_UID = "123456789"
_GatewayFor = Callable[[UUID], YandexGateway]
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
def gateway_for(channel: RedisExtensionChannel) -> _GatewayFor:
    builder = YandexExtensionGatewayBuilder(channel, batch_size=2)

    def build(user_id: UUID) -> YandexGateway:
        return builder(
            AccountAccess(
                account_id=uuid4(),
                user_id=user_id,
                platform=Platform.YANDEX,
                transport=Transport.EXTENSION,
                external_user_id=_UID,
                credentials=None,
            )
        )

    return build


def _device(channel: RedisExtensionChannel, user_id: UUID) -> HubDevice:
    return HubDevice(hub=channel, user_id=user_id, device_id=uuid4(), platform=FakeYandexBrowser())


def _browser(device: HubDevice) -> FakeYandexBrowser:
    assert isinstance(device.platform, FakeYandexBrowser)
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
    return [ExternalTrackRef(Platform.YANDEX, i) for i in ids]


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
    # дошла до Яндекса, а ответ снова потерян.
    device.lost.clear()
    device.lose_result_of.add("add_tracks")
    await device.go_online()
    async with _serving(device, stop_after_loss=True):
        ref = await gateway.create_playlist("Лайки SoundCloud", None, request_id="t-1:1")
        with pytest.raises(ExtensionUnavailableError):
            await gateway.add_tracks(ref, _refs("1:10", "2:20"))

    # 3) Повтор всей пачки (как run_write после паузы) — без дублей.
    device.lost.clear()
    await device.go_online()
    async with _serving(device):
        result = await gateway.add_tracks(ref, _refs("1:10", "2:20", "3:30"))

    [(kind, tracks)] = browser.lists.items()
    assert ref.external_id == f"{_UID}:{kind}"
    assert browser.executed.count("create_playlist") == 1
    assert tracks == ["1:10", "2:20", "3:30"]
    assert set(result.added) == set(_refs("1:10", "2:20", "3:30"))
    assert not result.failed


async def test_library_paged_and_likes_skip_already_liked(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    browser = _browser(device)
    browser.likes = ["2:20", "9:90", "8:80"]  # page_size=2 → две страницы
    gateway = gateway_for(user_id)
    await device.go_online()

    async with _serving(device):
        result = await gateway.add_to_library(_refs("1:10", "2:20", "3:30"))
        library = [t.ref.external_id async for t in gateway.get_library()]

    assert set(result.added) == set(_refs("1:10", "2:20", "3:30"))
    # Последний трек списка — на самом верху «Мне нравится».
    assert library == ["3:30", "1:10", "2:20", "9:90", "8:80"]
    assert browser.executed.count("library_ids_page") >= 4  # 2 страницы × 2 чтения


async def test_other_account_in_browser_is_session_mismatch(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    await device.go_online()
    browser = _browser(device)
    browser.external_user_id = "987654321"
    gateway = gateway_for(user_id)

    async with _serving(device):
        with pytest.raises(ExtensionUnavailableError) as raised:
            await gateway.create_playlist("Плейлист", None, request_id="t-2:1")

    assert raised.value.reason is ExtensionUnavailableReason.SESSION_MISMATCH
    assert browser.lists == {}


async def test_captcha_is_client_pause_and_server_bucket_untouched(
    channel: RedisExtensionChannel, gateway_for: _GatewayFor, redis: Redis
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    _browser(device).captcha = True
    gateway = gateway_for(user_id)
    await device.go_online()

    async with _serving(device):
        with pytest.raises(ExtensionUnavailableError) as raised:
            await gateway.search(TrackQuery(title="Starboy", artist="The Weeknd"))

    assert raised.value.reason is ExtensionUnavailableReason.CAPTCHA
    assert [key async for key in redis.scan_iter("ratelimit:*")] == []

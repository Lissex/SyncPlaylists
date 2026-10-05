"""Канал задач «воркер → расширение» на настоящем Redis (testcontainers): выдача задач
устройству, присутствие, пауза при закрытом браузере, таймауты по операциям,
идемпотентность создания плейлиста. Расширение — фейковое (tests/tools/fake_extension)
и работает с хабом напрямую, без WebSocket."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from syncplaylists.integrations.platforms.extension.gateway import ExtensionGateway
from syncplaylists.modules.extension.application.operations import OPERATIONS, OperationSpec
from syncplaylists.modules.extension.application.ports import (
    ExtensionCall,
    PendingPairing,
    PlatformPresence,
)
from syncplaylists.modules.extension.domain.value_objects import SessionState
from syncplaylists.modules.extension.infrastructure.pairing_store import RedisPairingStore
from syncplaylists.modules.extension.infrastructure.redis_channel import RedisExtensionChannel
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    ExtensionUnavailableReason,
    PlaylistNotFoundError,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform, PlaylistRef, Transport
from tests.tools.fake_extension import FakeBrowserPlatform, HubDevice

# Короткие таймауты, чтобы тест на таймаут не шёл полминуты.
_FAST = {
    name: OperationSpec(timeout_seconds=2, write=spec.write) for name, spec in OPERATIONS.items()
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


def _device(channel: RedisExtensionChannel, user_id: UUID) -> HubDevice:
    return HubDevice(
        hub=channel,
        user_id=user_id,
        device_id=uuid4(),
        platform=FakeBrowserPlatform(Platform.VK, external_user_id="vk-1"),
    )


def _call(user_id: UUID, operation: str = "search_by_isrc", **kwargs: object) -> ExtensionCall:
    args = kwargs.pop("args", {"isrc": "USUM71703861"})
    return ExtensionCall(
        user_id=user_id,
        platform=Platform.VK,
        external_user_id="vk-1",
        operation=operation,
        args=args,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def _gateway(channel: RedisExtensionChannel, user_id: UUID) -> ExtensionGateway:
    access = AccountAccess(
        account_id=uuid4(),
        user_id=user_id,
        platform=Platform.VK,
        transport=Transport.EXTENSION,
        external_user_id="vk-1",
        credentials=None,
    )
    return ExtensionGateway(channel, access)


@contextlib.asynccontextmanager
async def _serving(device: HubDevice, **kwargs: bool) -> AsyncIterator[asyncio.Task[None]]:
    task = asyncio.create_task(device.serve(**kwargs))
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_task_reaches_device_and_result_returns(channel: RedisExtensionChannel) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    await device.go_online()

    async with _serving(device):
        data = await channel.call(_call(user_id))

    assert [t["id"] for t in data["tracks"]] == ["vk-starboy"]


async def test_no_extension_online_fails_fast_as_offline(channel: RedisExtensionChannel) -> None:
    started = time.monotonic()

    with pytest.raises(ExtensionUnavailableError) as raised:
        await channel.call(_call(uuid4()))

    assert raised.value.reason is ExtensionUnavailableReason.OFFLINE
    assert time.monotonic() - started < 1  # задача никуда не ушла, ждать нечего


@pytest.mark.parametrize(
    ("session", "external_user_id", "reason"),
    [
        (SessionState.LOGGED_OUT, None, ExtensionUnavailableReason.LOGGED_OUT),
        (SessionState.CAPTCHA, "vk-1", ExtensionUnavailableReason.CAPTCHA),
        (SessionState.NO_PERMISSION, None, ExtensionUnavailableReason.NO_PERMISSION),
        (SessionState.OK, "vk-other", ExtensionUnavailableReason.SESSION_MISMATCH),
    ],
)
async def test_presence_state_explains_why_not_ready(
    channel: RedisExtensionChannel,
    session: SessionState,
    external_user_id: str | None,
    reason: ExtensionUnavailableReason,
) -> None:
    user_id = uuid4()
    await channel.publish_presence(
        user_id, uuid4(), [PlatformPresence(Platform.VK, session, external_user_id)], 60
    )

    with pytest.raises(ExtensionUnavailableError) as raised:
        await channel.call(_call(user_id))

    assert raised.value.reason is reason


async def test_browser_closed_mid_task_pauses_without_waiting_timeout(
    channel: RedisExtensionChannel,
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    device.lose_result_of.add("search_by_isrc")
    await device.go_online()

    started = time.monotonic()
    async with _serving(device, stop_after_loss=True):
        with pytest.raises(ExtensionUnavailableError) as raised:
            await channel.call(_call(user_id))

    assert raised.value.reason is ExtensionUnavailableReason.OFFLINE
    assert time.monotonic() - started < 2  # таймаут операции (2 с) не ждали


async def test_device_online_but_silent_times_out(channel: RedisExtensionChannel) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    await device.go_online()  # на связи, но задачи не выполняет

    with pytest.raises(ExtensionUnavailableError) as raised:
        await channel.call(_call(user_id))

    assert raised.value.reason is ExtensionUnavailableReason.TIMEOUT
    # Невыданная задача снята с очереди — переподключившемуся расширению не достанется.
    assert await channel.next_task(device.device_id, wait_seconds=1) is None


async def test_progress_extends_deadline(channel: RedisExtensionChannel, redis: Redis) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    await device.go_online()
    call = asyncio.create_task(channel.call(_call(user_id)))
    task = await channel.next_task(device.device_id, wait_seconds=2)
    assert task is not None

    for _ in range(3):  # 3 с работы при таймауте 2 с — progress держит задачу живой
        await asyncio.sleep(1)
        await channel.extend(device.device_id, task.task_id)
    await channel.complete(device.device_id, task.task_id, {"ok": True, "data": {"tracks": []}})

    assert await call == {"tracks": []}


async def test_unfinished_tasks_are_redelivered_after_reconnect(
    channel: RedisExtensionChannel,
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    await device.go_online()
    call = asyncio.create_task(channel.call(_call(user_id)))
    first = await channel.next_task(device.device_id, wait_seconds=2)
    assert first is not None

    await channel.requeue_inflight(device.device_id)  # связь оборвалась и восстановилась
    again = await channel.next_task(device.device_id, wait_seconds=2)

    assert again is not None
    assert again.task_id == first.task_id
    await channel.complete(device.device_id, again.task_id, {"ok": True, "data": {"tracks": []}})
    assert await call == {"tracks": []}


async def test_result_from_other_device_is_ignored(channel: RedisExtensionChannel) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    await device.go_online()
    call = asyncio.create_task(channel.call(_call(user_id)))
    task = await channel.next_task(device.device_id, wait_seconds=2)
    assert task is not None

    assert not await channel.complete(uuid4(), task.task_id, {"ok": True, "data": {"x": 1}})
    await channel.complete(device.device_id, task.task_id, {"ok": True, "data": {"tracks": []}})
    assert await call == {"tracks": []}


async def test_platform_error_from_extension_is_raised(channel: RedisExtensionChannel) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    await device.go_online()

    async with _serving(device):
        with pytest.raises(PlaylistNotFoundError):
            await _gateway(channel, user_id).playlist_info(PlaylistRef(Platform.VK, "missing"))


# --- обязательный тест 4c-1: идемпотентность создания плейлиста ---------------------


async def test_create_playlist_result_lost_retry_same_key_creates_one_playlist(
    channel: RedisExtensionChannel,
) -> None:
    """Задача выполнена в браузере, результат потерян (браузер закрыли сразу после
    создания), повтор с тем же request_id → на площадке один плейлист. Ответ на повтор
    даёт журнал расширения — второй раз операция не выполняется."""
    user_id = uuid4()
    device = _device(channel, user_id)
    device.lose_result_of.add("create_playlist")
    gateway = _gateway(channel, user_id)
    await device.go_online()

    async with _serving(device, stop_after_loss=True):
        with pytest.raises(ExtensionUnavailableError):
            await gateway.create_playlist("Копия", None, request_id="transfer-1:1")
    assert len(device.platform.playlists) == 1  # в браузере плейлист уже создан

    device.lost.clear()  # результат так и не дошёл до сервера
    await device.go_online()
    async with _serving(device):
        ref = await gateway.create_playlist("Копия", None, request_id="transfer-1:1")

    assert len(device.platform.playlists) == 1
    assert ref.external_id in device.platform.playlists
    assert device.platform.executed.count("create_playlist") == 1


async def test_late_result_is_kept_and_retry_does_not_reach_browser(
    channel: RedisExtensionChannel,
) -> None:
    """Тот же сценарий, но расширение после переподключения досылает потерянный
    результат: сервер сохраняет его по ключу, и повтор вообще не уходит в браузер."""
    user_id = uuid4()
    device = _device(channel, user_id)
    device.lose_result_of.add("create_playlist")
    gateway = _gateway(channel, user_id)
    await device.go_online()

    async with _serving(device, stop_after_loss=True):
        with pytest.raises(ExtensionUnavailableError):
            await gateway.create_playlist("Копия", None, request_id="transfer-2:1")

    await device.go_online()
    await device.flush_lost()
    ref = await gateway.create_playlist("Копия", None, request_id="transfer-2:1")

    assert list(device.platform.playlists) == [ref.external_id]
    assert await channel.next_task(device.device_id, wait_seconds=1) is None


async def test_different_request_ids_create_different_playlists(
    channel: RedisExtensionChannel,
) -> None:
    user_id = uuid4()
    device = _device(channel, user_id)
    gateway = _gateway(channel, user_id)
    await device.go_online()

    async with _serving(device):
        first = await gateway.create_playlist("Копия (1/2)", None, request_id="t:1")
        second = await gateway.create_playlist("Копия (2/2)", None, request_id="t:2")

    assert first != second
    assert len(device.platform.playlists) == 2


# --- привязка: код одноразовый (Lua) ------------------------------------------------


async def test_pairing_code_confirms_once(redis: Redis) -> None:
    store = RedisPairingStore(redis)
    pending = PendingPairing(device_name="Chrome", browser="chrome", version="0.1")
    assert await store.create("pid-hash", "code-hash", pending, 60)
    assert not await store.create("other", "code-hash", pending, 60)  # код занят

    user_id = uuid4()
    assert await store.confirm("code-hash", user_id)
    assert not await store.confirm("code-hash", uuid4())

    confirmed = await store.get("pid-hash")
    assert confirmed is not None
    assert confirmed.user_id == user_id
    assert await store.take("pid-hash") is not None
    assert await store.take("pid-hash") is None

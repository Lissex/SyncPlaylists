"""Этап 5-0: статус расширений для сайта (экран «Подключения», подсказки на паузах)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from syncplaylists.modules.extension.application.ports import (
    DevicePlatformState,
    PlatformPresence,
)
from syncplaylists.modules.extension.application.use_cases import GetExtensionStatusUseCase
from syncplaylists.modules.extension.domain.entities import ExtensionDevice
from syncplaylists.modules.extension.domain.value_objects import SessionState
from syncplaylists.shared_kernel.domain.value_objects import Platform
from tests.fakes.extension import InMemoryDeviceRepository

_USER = uuid4()


class _Presence:
    def __init__(self, online: set[UUID], states: list[DevicePlatformState]) -> None:
        self._online = online
        self._states = states

    async def online(self, device_ids: Sequence[UUID]) -> set[UUID]:
        return {d for d in device_ids if d in self._online}

    async def presence(self, user_id: UUID) -> list[DevicePlatformState]:
        return self._states


def _device(name: str, *, revoked: bool = False) -> ExtensionDevice:
    now = datetime.now(UTC)
    return ExtensionDevice(
        id=uuid4(),
        user_id=_USER,
        name=name,
        browser="chrome",
        version="0.1.0",
        token_hash=name,
        created_at=now,
        revoked_at=now if revoked else None,
    )


def _state(
    device: ExtensionDevice, platform: Platform, session: SessionState
) -> DevicePlatformState:
    return DevicePlatformState(device.id, PlatformPresence(platform, session, f"uid-{device.name}"))


async def test_status_lists_active_devices_and_best_platform_state() -> None:
    devices = InMemoryDeviceRepository()
    home, work, old = _device("home"), _device("work"), _device("old", revoked=True)
    for device in (home, work, old):
        await devices.add(device)
    presence = _Presence(
        online={home.id, work.id, old.id},
        states=[
            _state(home, Platform.YANDEX, SessionState.CAPTCHA),
            _state(work, Platform.YANDEX, SessionState.OK),  # ok важнее капчи
            _state(home, Platform.VK, SessionState.LOGGED_OUT),
            _state(old, Platform.SOUNDCLOUD, SessionState.OK),  # отозванное — не в счёт
        ],
    )

    status = await GetExtensionStatusUseCase(devices, presence).execute(_USER)

    assert {d.device.name: d.online for d in status.devices} == {"home": True, "work": True}
    by_platform = {p.platform: p for p in status.platforms}
    assert set(by_platform) == set(Platform)
    assert by_platform[Platform.YANDEX].state is SessionState.OK
    assert by_platform[Platform.YANDEX].device_id == work.id
    assert by_platform[Platform.YANDEX].external_user_id == "uid-work"
    assert by_platform[Platform.VK].state is SessionState.LOGGED_OUT
    assert by_platform[Platform.SOUNDCLOUD].state is None
    assert by_platform[Platform.SPOTIFY].state is None


async def test_offline_device_is_reported_offline() -> None:
    devices = InMemoryDeviceRepository()
    device = _device("laptop")
    await devices.add(device)

    status = await GetExtensionStatusUseCase(devices, _Presence(set(), [])).execute(_USER)

    assert [(d.device.id, d.online) for d in status.devices] == [(device.id, False)]

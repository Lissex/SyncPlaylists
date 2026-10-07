"""Проверка связи с расширением (diagnostics.echo): постановка в очередь client, проверка
ответа строгой схемой, ошибки устройства."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from syncplaylists.modules.extension.application.diagnostics import (
    DIAGNOSTICS_PAGE_PATH,
    GetProbeUseCase,
    RunProbeUseCase,
    StartProbeUseCase,
)
from syncplaylists.modules.extension.application.ports import ProbeRecord
from syncplaylists.modules.extension.application.session import DeviceSession
from syncplaylists.modules.extension.domain.entities import ExtensionDevice
from syncplaylists.modules.extension.domain.errors import (
    DeviceNotFoundError,
    DeviceUnavailableError,
)
from syncplaylists.shared_kernel.application.ports import TaskLane
from tests.fakes import FakeTaskQueue
from tests.fakes.extension import InMemoryDeviceRepository, RecordingHub


class _Store:
    def __init__(self) -> None:
        self.records: dict[str, ProbeRecord] = {}

    async def save(self, probe_id: str, record: ProbeRecord, ttl_seconds: int) -> None:
        self.records[probe_id] = record

    async def get(self, probe_id: str) -> ProbeRecord | None:
        return self.records.get(probe_id)


class _Caller:
    def __init__(self, outcome: Mapping[str, Any] | Exception) -> None:
        self.outcome = outcome
        self.calls: list[tuple[UUID, UUID, str, dict[str, Any]]] = []

    async def call_device(
        self,
        user_id: UUID,
        device_id: UUID,
        operation: str,
        args: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        self.calls.append((user_id, device_id, operation, dict(args)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _device(user_id: UUID) -> ExtensionDevice:
    return ExtensionDevice(
        id=uuid4(),
        user_id=user_id,
        name="Chrome",
        browser="chrome",
        version="0.1.0",
        token_hash="h",
        created_at=datetime.now(UTC),
    )


async def test_start_probe_enqueues_into_client_lane() -> None:
    devices, store, queue = InMemoryDeviceRepository(), _Store(), FakeTaskQueue()
    user_id = uuid4()
    device = _device(user_id)
    await devices.add(device)

    probe_id = await StartProbeUseCase(devices, store, queue).execute(user_id, device.id)

    assert store.records[probe_id].status == "pending"
    assert queue.lanes == [("probe_extension", TaskLane.CLIENT)]
    _, args = queue.enqueued[0]
    assert args[:3] == (probe_id, user_id, device.id)


@pytest.mark.parametrize("case", ["foreign", "revoked", "missing"])
async def test_start_probe_only_for_own_active_device(case: str) -> None:
    devices, store, queue = InMemoryDeviceRepository(), _Store(), FakeTaskQueue()
    user_id = uuid4()
    device = _device(uuid4() if case == "foreign" else user_id)
    if case == "revoked":
        device.revoke(datetime.now(UTC))
    if case != "missing":
        await devices.add(device)

    with pytest.raises(DeviceNotFoundError):
        await StartProbeUseCase(devices, store, queue).execute(user_id, device.id)
    assert queue.enqueued == []


def _echo(nonce: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {"nonce": nonce, "page_title": "t", "path": DIAGNOSTICS_PAGE_PATH, **extra},
    }


async def _run(outcome: Mapping[str, Any] | Exception, nonce: str = "n1") -> ProbeRecord:
    store = _Store()
    caller = _Caller(outcome)
    await RunProbeUseCase(caller, store).execute("p", uuid4(), uuid4(), nonce)
    assert caller.calls[0][2:] == ("diagnostics.echo", {"nonce": nonce})
    return store.records["p"]


async def test_valid_echo_is_ok() -> None:
    record = await _run(_echo("n1"))

    assert record.status == "ok"
    assert record.data == {"page_title": "t", "path": DIAGNOSTICS_PAGE_PATH}


@pytest.mark.parametrize(
    "outcome",
    [
        _echo("другой nonce"),
        _echo("n1", cookie="sessionid=secret"),  # лишнее поле — отвергается
        {"ok": True, "data": {"nonce": "n1", "page_title": "t", "path": "/other"}},
        {"ok": True, "data": None},
    ],
)
async def test_bad_echo_is_rejected(outcome: Mapping[str, Any]) -> None:
    record = await _run(outcome)

    assert record.status == "error"
    assert record.error == "bad_result"
    assert record.data is None


async def test_extension_error_and_unavailable_device() -> None:
    error = await _run({"ok": False, "error": {"code": "no_permission"}})
    offline = await _run(DeviceUnavailableError("offline"))

    assert (error.status, error.error) == ("error", "no_permission")
    assert (offline.status, offline.error) == ("error", "offline")


async def test_probe_is_visible_only_to_owner() -> None:
    store = _Store()
    owner = uuid4()
    await store.save("p", ProbeRecord(owner, uuid4(), "pending"), 60)

    assert await GetProbeUseCase(store).execute(owner, "p") is not None
    assert await GetProbeUseCase(store).execute(uuid4(), "p") is None


async def test_session_marks_device_online_until_close() -> None:
    hub = RecordingHub()
    session = DeviceSession(
        hub, FakeTaskQueue(), user_id=uuid4(), device_id=uuid4(), presence_ttl_seconds=60
    )

    await session.start()
    assert hub.online == {session.device_id: session.user_id}
    await session.close()
    assert hub.online == {}

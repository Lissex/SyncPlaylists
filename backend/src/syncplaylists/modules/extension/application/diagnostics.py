"""Проверка связи с расширением (этап 4c-2, только dev — EXTENSION__DIAGNOSTICS_ENABLED):
тестовая операция `diagnostics.echo` проходит весь путь сервер → очередь client → воркер
→ WebSocket → расширение → фоновая вкладка → MAIN world → валидация в расширении →
сервер, без площадок. Вкладка — страница нашего же API (DIAGNOSTICS_PAGE_PATH): она под
обязательным host permission, лишних разрешений проба не требует."""

import secrets
from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

from syncplaylists.modules.extension.application.ports import (
    DeviceCaller,
    ExtensionDeviceRepository,
    ProbeRecord,
    ProbeStore,
)
from syncplaylists.modules.extension.domain.errors import (
    DeviceNotFoundError,
    DeviceUnavailableError,
)
from syncplaylists.shared_kernel.application.ports import TaskLane, TaskQueue

PROBE_EXTENSION_TASK: Final = "probe_extension"
DIAGNOSTIC_ECHO_OP: Final = "diagnostics.echo"
DIAGNOSTICS_PAGE_PATH: Final = "/extension/diagnostics/page"
DIAGNOSTICS_PAGE_TITLE: Final = "SyncPlaylists — проверка связи"
_PROBE_TTL_SECONDS: Final = 600
_ECHO_TIMEOUT_SECONDS: Final = 30.0
_ECHO_FIELDS: Final = frozenset({"nonce", "page_title", "path"})


class StartProbeUseCase:
    def __init__(
        self, devices: ExtensionDeviceRepository, store: ProbeStore, task_queue: TaskQueue
    ) -> None:
        self._devices = devices
        self._store = store
        self._task_queue = task_queue

    async def execute(self, user_id: UUID, device_id: UUID) -> str:
        """Бросает DeviceNotFoundError (нет, чужое или отозвано)."""
        device = await self._devices.get(device_id)
        if device is None or device.user_id != user_id or device.revoked_at is not None:
            raise DeviceNotFoundError(str(device_id))
        probe_id = secrets.token_urlsafe(16)
        nonce = secrets.token_hex(8)
        await self._store.save(
            probe_id, ProbeRecord(user_id, device_id, "pending"), _PROBE_TTL_SECONDS
        )
        # Очередь client — та же, что у переносов через расширение: проба проверяет и её.
        await self._task_queue.enqueue(
            PROBE_EXTENSION_TASK, probe_id, user_id, device_id, nonce, lane=TaskLane.CLIENT
        )
        return probe_id


class RunProbeUseCase:
    """ARQ-задача probe_extension: отдать echo устройству и проверить ответ."""

    def __init__(self, caller: DeviceCaller, store: ProbeStore) -> None:
        self._caller = caller
        self._store = store

    async def execute(self, probe_id: str, user_id: UUID, device_id: UUID, nonce: str) -> None:
        try:
            outcome = await self._caller.call_device(
                user_id, device_id, DIAGNOSTIC_ECHO_OP, {"nonce": nonce}, _ECHO_TIMEOUT_SECONDS
            )
        except DeviceUnavailableError as exc:
            await self._finish(probe_id, user_id, device_id, error=exc.reason)
            return
        if not outcome.get("ok"):
            error = outcome.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            await self._finish(probe_id, user_id, device_id, error=str(code or "error")[:40])
            return
        data = _valid_echo(outcome.get("data"), nonce)
        if data is None:
            await self._finish(probe_id, user_id, device_id, error="bad_result")
            return
        await self._finish(probe_id, user_id, device_id, data=data)

    async def _finish(
        self,
        probe_id: str,
        user_id: UUID,
        device_id: UUID,
        *,
        data: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        record = ProbeRecord(user_id, device_id, "error" if error else "ok", data, error)
        await self._store.save(probe_id, record, _PROBE_TTL_SECONDS)


def _valid_echo(data: object, nonce: str) -> dict[str, str] | None:
    """Ответ строго {nonce, page_title, path} строками; nonce наш, страница — наша."""
    if not isinstance(data, Mapping) or set(data) != _ECHO_FIELDS:
        return None
    if not all(isinstance(value, str) and len(value) <= 200 for value in data.values()):
        return None
    if data["nonce"] != nonce or data["path"] != DIAGNOSTICS_PAGE_PATH:
        return None
    return {"page_title": data["page_title"], "path": data["path"]}


class GetProbeUseCase:
    def __init__(self, store: ProbeStore) -> None:
        self._store = store

    async def execute(self, user_id: UUID, probe_id: str) -> ProbeRecord | None:
        record = await self._store.get(probe_id)
        if record is None or record.user_id != user_id:
            return None
        return record

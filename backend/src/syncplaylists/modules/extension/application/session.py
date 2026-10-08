from collections.abc import Mapping
from typing import Any
from uuid import UUID

from syncplaylists.modules.extension.application.ports import (
    DeliveredTask,
    ExtensionHub,
    PlatformPresence,
)
from syncplaylists.modules.extension.domain.value_objects import SessionState
from syncplaylists.shared_kernel.application.ports import TaskQueue
from syncplaylists.shared_kernel.application.tasks import RESUME_CLIENT_TRANSFERS
from syncplaylists.shared_kernel.domain.value_objects import Platform


class DeviceSession:
    """Одно подключение расширения (WebSocket): что оно сообщает о площадках, какие
    задачи ему выдать, куда положить результаты.

    Как только площадка в браузере становится готова (подключились заново после
    закрытого браузера, вошли на площадку, прошли капчу, выдали разрешение), ставится
    задача resume_client_transfers — переносы, ждущие браузер, продолжатся сами."""

    def __init__(
        self,
        hub: ExtensionHub,
        task_queue: TaskQueue,
        *,
        user_id: UUID,
        device_id: UUID,
        presence_ttl_seconds: int,
    ) -> None:
        self._hub = hub
        self._task_queue = task_queue
        self.user_id = user_id
        self.device_id = device_id
        self._presence_ttl = presence_ttl_seconds
        self._states: dict[Platform, PlatformPresence] = {}

    async def start(self) -> None:
        await self._hub.requeue_inflight(self.device_id)
        await self._hub.mark_online(self.user_id, self.device_id, self._presence_ttl)

    async def report(self, presence: PlatformPresence) -> None:
        previous = self._states.get(presence.platform)
        self._states[presence.platform] = presence
        await self._hub.publish_presence(
            self.user_id, self.device_id, [presence], self._presence_ttl
        )
        became_ready = presence.session is SessionState.OK and (
            previous is None
            or previous.session is not SessionState.OK
            or previous.external_user_id != presence.external_user_id
        )
        if became_ready:
            await self._task_queue.enqueue(
                RESUME_CLIENT_TRANSFERS, self.user_id, presence.platform.value
            )

    async def heartbeat(self) -> None:
        await self._hub.mark_online(self.user_id, self.device_id, self._presence_ttl)
        if self._states:
            await self._hub.publish_presence(
                self.user_id, self.device_id, list(self._states.values()), self._presence_ttl
            )

    async def next_task(self, wait_seconds: float) -> DeliveredTask | None:
        return await self._hub.next_task(self.device_id, wait_seconds)

    async def complete(self, task_id: str, outcome: Mapping[str, Any]) -> bool:
        return await self._hub.complete(self.device_id, task_id, outcome)

    async def progress(self, task_id: str) -> None:
        await self._hub.extend(self.device_id, task_id)

    async def close(self) -> None:
        await self._hub.drop_online(self.device_id)
        if self._states:
            await self._hub.drop_presence(self.user_id, self.device_id, list(self._states))

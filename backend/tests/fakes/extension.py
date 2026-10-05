import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from syncplaylists.modules.extension.application.ports import (
    DeliveredTask,
    ExtensionCall,
    PendingPairing,
    PlatformPresence,
)
from syncplaylists.modules.extension.domain.entities import ExtensionDevice
from syncplaylists.shared_kernel.domain.value_objects import Platform


class InMemoryPairingStore:
    def __init__(self) -> None:
        self.pairings: dict[str, PendingPairing] = {}
        self.codes: dict[str, str] = {}

    async def create(
        self, pairing_id_hash: str, code_hash: str, pairing: PendingPairing, ttl_seconds: int
    ) -> bool:
        if code_hash in self.codes:
            return False
        self.codes[code_hash] = pairing_id_hash
        self.pairings[pairing_id_hash] = pairing
        return True

    async def confirm(self, code_hash: str, user_id: UUID) -> bool:
        pairing_id_hash = self.codes.pop(code_hash, None)
        pending = self.pairings.get(pairing_id_hash) if pairing_id_hash else None
        if pairing_id_hash is None or pending is None or pending.user_id is not None:
            return False
        self.pairings[pairing_id_hash] = dataclasses.replace(pending, user_id=user_id)
        return True

    async def get(self, pairing_id_hash: str) -> PendingPairing | None:
        return self.pairings.get(pairing_id_hash)

    async def take(self, pairing_id_hash: str) -> PendingPairing | None:
        return self.pairings.pop(pairing_id_hash, None)


class InMemoryDeviceRepository:
    def __init__(self) -> None:
        self.devices: dict[UUID, ExtensionDevice] = {}

    async def add(self, device: ExtensionDevice) -> None:
        self.devices[device.id] = device

    async def get(self, device_id: UUID) -> ExtensionDevice | None:
        return self.devices.get(device_id)

    async def find_by_token_hash(self, token_hash: str) -> ExtensionDevice | None:
        return next((d for d in self.devices.values() if d.token_hash == token_hash), None)

    async def list_for_user(self, user_id: UUID) -> list[ExtensionDevice]:
        return [d for d in self.devices.values() if d.user_id == user_id]

    async def save(self, device: ExtensionDevice) -> None:
        self.devices[device.id] = device


class RecordingHub:
    def __init__(self) -> None:
        self.presence: list[tuple[UUID, UUID, list[PlatformPresence]]] = []
        self.dropped: list[tuple[UUID, UUID, list[Platform]]] = []
        self.requeued: list[UUID] = []
        self.completed: list[tuple[UUID, str, dict[str, Any]]] = []
        self.extended: list[tuple[UUID, str]] = []
        self.tasks: list[DeliveredTask] = []

    async def publish_presence(
        self,
        user_id: UUID,
        device_id: UUID,
        presence: Sequence[PlatformPresence],
        ttl_seconds: int,
    ) -> None:
        self.presence.append((user_id, device_id, list(presence)))

    async def drop_presence(
        self, user_id: UUID, device_id: UUID, platforms: Sequence[Platform]
    ) -> None:
        self.dropped.append((user_id, device_id, list(platforms)))

    async def requeue_inflight(self, device_id: UUID) -> None:
        self.requeued.append(device_id)

    async def next_task(self, device_id: UUID, wait_seconds: float) -> DeliveredTask | None:
        return self.tasks.pop(0) if self.tasks else None

    async def complete(self, device_id: UUID, task_id: str, outcome: Mapping[str, Any]) -> bool:
        self.completed.append((device_id, task_id, dict(outcome)))
        return True

    async def extend(self, device_id: UUID, task_id: str) -> None:
        self.extended.append((device_id, task_id))


class ScriptedChannel:
    """ExtensionChannel для тестов шлюза: ответы по операции (по очереди) или ошибка."""

    def __init__(self) -> None:
        self.responses: dict[str, list[Mapping[str, Any] | Exception]] = {}
        self.calls: list[ExtensionCall] = []

    def reply(self, operation: str, *responses: Mapping[str, Any] | Exception) -> None:
        self.responses.setdefault(operation, []).extend(responses)

    async def call(self, call: ExtensionCall) -> Mapping[str, Any]:
        self.calls.append(call)
        response = self.responses[call.operation].pop(0)
        if isinstance(response, Exception):
            raise response
        return response

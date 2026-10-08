from dataclasses import dataclass
from uuid import UUID

from syncplaylists.shared_kernel.domain.base import DomainEvent


@dataclass(frozen=True, slots=True)
class ExtensionDevicePaired(DomainEvent):
    device_id: UUID
    user_id: UUID


@dataclass(frozen=True, slots=True)
class ExtensionDeviceRevoked(DomainEvent):
    device_id: UUID
    user_id: UUID

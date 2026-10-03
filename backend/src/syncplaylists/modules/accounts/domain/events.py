from dataclasses import dataclass
from uuid import UUID

from syncplaylists.shared_kernel.domain.base import DomainEvent
from syncplaylists.shared_kernel.domain.value_objects import Platform


@dataclass(frozen=True, slots=True)
class AccountConnected(DomainEvent):
    account_id: UUID
    user_id: UUID
    platform: Platform


@dataclass(frozen=True, slots=True)
class AccountDisconnected(DomainEvent):
    account_id: UUID
    user_id: UUID
    platform: Platform

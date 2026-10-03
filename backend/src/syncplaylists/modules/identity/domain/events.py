from dataclasses import dataclass
from uuid import UUID

from syncplaylists.shared_kernel.domain.base import DomainEvent


@dataclass(frozen=True, slots=True)
class UserRegistered(DomainEvent):
    user_id: UUID

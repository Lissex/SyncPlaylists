from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from syncplaylists.modules.identity.domain.entities import User


@dataclass(frozen=True, slots=True)
class UserDto:
    id: UUID
    email: str
    created_at: datetime

    @classmethod
    def from_domain(cls, user: User) -> "UserDto":
        return cls(id=user.id, email=user.email.value, created_at=user.created_at)


@dataclass(frozen=True, slots=True)
class AuthResult:
    user: UserDto
    session_token: str

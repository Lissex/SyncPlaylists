from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from syncplaylists.modules.identity.domain.events import UserRegistered
from syncplaylists.modules.identity.domain.value_objects import Email
from syncplaylists.shared_kernel.domain.base import AggregateRoot


@dataclass(eq=False, slots=True)
class User(AggregateRoot):
    email: Email
    # Хеш непрозрачен для домена (формат задаёт PasswordHasher в application/infra).
    password_hash: str
    created_at: datetime

    @classmethod
    def register(cls, user_id: UUID, email: Email, password_hash: str, now: datetime) -> "User":
        user = cls(id=user_id, email=email, password_hash=password_hash, created_at=now)
        user.record_event(UserRegistered(occurred_at=now, user_id=user_id))
        return user

    def change_password_hash(self, password_hash: str) -> None:
        self.password_hash = password_hash

from typing import Protocol
from uuid import UUID

from syncplaylists.modules.identity.domain.entities import User
from syncplaylists.modules.identity.domain.value_objects import Email


class UserRepository(Protocol):
    async def get(self, user_id: UUID) -> User | None: ...

    async def get_by_email(self, email: Email) -> User | None: ...

    # Бросает EmailAlreadyRegisteredError при гонке двух регистраций одного email.
    async def add(self, user: User) -> None: ...

    async def save(self, user: User) -> None: ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...

    def needs_rehash(self, password_hash: str) -> bool: ...


class SessionTokenService(Protocol):
    """Сессии: непрозрачный случайный токен (в cookie) ↔ user_id на стороне сервера."""

    async def issue(self, user_id: UUID) -> str: ...

    async def resolve(self, token: str) -> UUID | None: ...

    async def revoke(self, token: str) -> None: ...

    # «Выйти со всех устройств».
    async def revoke_all(self, user_id: UUID) -> None: ...


class AttemptLimiter(Protocol):
    # Засчитывает попытку по ключу; возвращает через сколько секунд можно повторить,
    # если лимит окна превышен, иначе None.
    async def hit(self, key: str) -> int | None: ...

import secrets
from uuid import UUID

from syncplaylists.modules.identity.domain.entities import User
from syncplaylists.modules.identity.domain.errors import EmailAlreadyRegisteredError
from syncplaylists.modules.identity.domain.value_objects import Email


class InMemoryUserRepository:
    def __init__(self) -> None:
        self.storage: dict[UUID, User] = {}

    async def get(self, user_id: UUID) -> User | None:
        return self.storage.get(user_id)

    async def get_by_email(self, email: Email) -> User | None:
        return next((u for u in self.storage.values() if u.email == email), None)

    async def add(self, user: User) -> None:
        if await self.get_by_email(user.email) is not None:
            raise EmailAlreadyRegisteredError(user.email.value)
        self.storage[user.id] = user

    async def save(self, user: User) -> None:
        self.storage[user.id] = user


class FakePasswordHasher:
    """`hashed:<scheme>:<password>`; needs_rehash — если схема устарела."""

    def __init__(self, scheme: str = "v2") -> None:
        self.scheme = scheme
        self.verify_calls = 0

    def hash(self, password: str) -> str:
        return f"hashed:{self.scheme}:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        self.verify_calls += 1
        return password_hash.split(":", 2)[-1] == password

    def needs_rehash(self, password_hash: str) -> bool:
        return not password_hash.startswith(f"hashed:{self.scheme}:")


class InMemorySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, UUID] = {}

    async def issue(self, user_id: UUID) -> str:
        token = secrets.token_urlsafe(16)
        self.sessions[token] = user_id
        return token

    async def resolve(self, token: str) -> UUID | None:
        return self.sessions.get(token)

    async def revoke(self, token: str) -> None:
        self.sessions.pop(token, None)

    async def revoke_all(self, user_id: UUID) -> None:
        self.sessions = {t: u for t, u in self.sessions.items() if u != user_id}


class FakeAttemptLimiter:
    def __init__(self, attempts: int = 5) -> None:
        self._attempts = attempts
        self.counts: dict[str, int] = {}

    async def hit(self, key: str) -> int | None:
        self.counts[key] = self.counts.get(key, 0) + 1
        return 60 if self.counts[key] > self._attempts else None

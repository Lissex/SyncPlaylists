from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.identity.application.use_cases import (
    GetCurrentUserUseCase,
    LoginUseCase,
    LogoutAllUseCase,
    LogoutUseCase,
    RegisterUserUseCase,
    ResolveSessionUseCase,
    WeakPasswordError,
)
from syncplaylists.modules.identity.domain.entities import User
from syncplaylists.modules.identity.domain.errors import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    TooManyAttemptsError,
)
from syncplaylists.modules.identity.domain.value_objects import Email
from tests.fakes import FakeUnitOfWork
from tests.fakes.identity import (
    FakeAttemptLimiter,
    FakePasswordHasher,
    InMemorySessionStore,
    InMemoryUserRepository,
)

_IP = "10.0.0.1"
_PASSWORD = "correct horse battery"


class Env:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.users = InMemoryUserRepository()
        self.hasher = FakePasswordHasher()
        self.sessions = InMemorySessionStore()
        self.limiter = FakeAttemptLimiter(attempts=5)

    def register(self) -> RegisterUserUseCase:
        return RegisterUserUseCase(self.uow, self.users, self.hasher, self.sessions, self.limiter)

    def login(self) -> LoginUseCase:
        return LoginUseCase(self.uow, self.users, self.hasher, self.sessions, self.limiter)

    async def seed_user(self, email: str = "alice@example.com", scheme: str = "v2") -> User:
        user = User.register(
            uuid4(), Email(email), f"hashed:{scheme}:{_PASSWORD}", datetime.now(UTC)
        )
        await self.users.add(user)
        return user


async def test_register_creates_user_with_hashed_password_and_session() -> None:
    env = Env()

    result = await env.register().execute(" Alice@Example.com ", _PASSWORD, _IP)

    stored = await env.users.get(result.user.id)
    assert stored is not None
    assert stored.email.value == "alice@example.com"
    assert stored.password_hash == f"hashed:v2:{_PASSWORD}"
    assert stored.password_hash != _PASSWORD
    assert env.sessions.sessions[result.session_token] == result.user.id
    assert env.uow.commits == 1


async def test_register_rejects_duplicate_email() -> None:
    env = Env()
    await env.seed_user("alice@example.com")

    with pytest.raises(EmailAlreadyRegisteredError):
        await env.register().execute("ALICE@example.com", _PASSWORD, _IP)


async def test_register_rejects_short_password() -> None:
    env = Env()

    with pytest.raises(WeakPasswordError):
        await env.register().execute("alice@example.com", "short", _IP)


async def test_login_returns_session_for_valid_credentials() -> None:
    env = Env()
    user = await env.seed_user()

    result = await env.login().execute("alice@example.com", _PASSWORD, _IP)

    assert result.user.id == user.id
    assert env.sessions.sessions[result.session_token] == user.id


async def test_login_rejects_wrong_password() -> None:
    env = Env()
    await env.seed_user()

    with pytest.raises(InvalidCredentialsError):
        await env.login().execute("alice@example.com", "wrong password", _IP)


async def test_login_with_unknown_email_still_verifies_a_hash() -> None:
    # Время ответа не должно выдавать, зарегистрирован ли email.
    env = Env()
    use_case = env.login()

    with pytest.raises(InvalidCredentialsError):
        await use_case.execute("nobody@example.com", _PASSWORD, _IP)

    assert env.hasher.verify_calls == 1


async def test_login_with_malformed_email_is_invalid_credentials() -> None:
    env = Env()

    with pytest.raises(InvalidCredentialsError):
        await env.login().execute("not-an-email", _PASSWORD, _IP)


async def test_login_rehashes_outdated_hash() -> None:
    env = Env()
    user = await env.seed_user(scheme="v1")

    await env.login().execute("alice@example.com", _PASSWORD, _IP)

    stored = await env.users.get(user.id)
    assert stored is not None
    assert stored.password_hash == f"hashed:v2:{_PASSWORD}"


async def test_login_is_rate_limited_before_password_check() -> None:
    env = Env()
    await env.seed_user()
    use_case = env.login()
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            await use_case.execute("alice@example.com", "wrong password", _IP)
    verify_calls = env.hasher.verify_calls

    with pytest.raises(TooManyAttemptsError) as exc_info:
        await use_case.execute("alice@example.com", _PASSWORD, _IP)

    assert exc_info.value.retry_after_s == 60
    assert env.hasher.verify_calls == verify_calls  # до хешера дело не дошло


async def test_rate_limit_key_is_per_ip_and_email_without_raw_email() -> None:
    env = Env()
    use_case = env.login()
    with pytest.raises(InvalidCredentialsError):
        await use_case.execute("Alice@Example.com", _PASSWORD, _IP)
    with pytest.raises(InvalidCredentialsError):
        await use_case.execute("bob@example.com", _PASSWORD, _IP)
    with pytest.raises(InvalidCredentialsError):
        await use_case.execute("alice@example.com", _PASSWORD, "10.0.0.2")

    assert len(env.limiter.counts) == 3
    assert all("alice" not in key and "bob" not in key for key in env.limiter.counts)


async def test_register_is_rate_limited() -> None:
    env = Env()
    use_case = env.register()
    for _ in range(5):
        with pytest.raises(WeakPasswordError):
            await use_case.execute("alice@example.com", "short", _IP)

    with pytest.raises(TooManyAttemptsError):
        await use_case.execute("alice@example.com", _PASSWORD, _IP)


async def test_resolve_session_returns_user_id() -> None:
    env = Env()
    user = await env.seed_user()
    token = await env.sessions.issue(user.id)

    assert await ResolveSessionUseCase(env.users, env.sessions).execute(token) == user.id


async def test_resolve_session_revokes_session_of_deleted_user() -> None:
    env = Env()
    token = await env.sessions.issue(uuid4())  # пользователя нет

    assert await ResolveSessionUseCase(env.users, env.sessions).execute(token) is None
    assert token not in env.sessions.sessions


async def test_resolve_unknown_token_is_none() -> None:
    env = Env()

    assert await ResolveSessionUseCase(env.users, env.sessions).execute("nope") is None


async def test_logout_revokes_only_current_session() -> None:
    env = Env()
    user = await env.seed_user()
    first = await env.sessions.issue(user.id)
    second = await env.sessions.issue(user.id)

    await LogoutUseCase(env.sessions).execute(first)

    assert first not in env.sessions.sessions
    assert second in env.sessions.sessions


async def test_logout_all_revokes_every_session_of_user_only() -> None:
    env = Env()
    alice = await env.seed_user("alice@example.com")
    bob = await env.seed_user("bob@example.com")
    await env.sessions.issue(alice.id)
    await env.sessions.issue(alice.id)
    bob_token = await env.sessions.issue(bob.id)

    await LogoutAllUseCase(env.sessions).execute(alice.id)

    assert list(env.sessions.sessions) == [bob_token]


async def test_get_current_user() -> None:
    env = Env()
    user = await env.seed_user()

    dto = await GetCurrentUserUseCase(env.users).execute(user.id)

    assert dto is not None
    assert dto.email == "alice@example.com"

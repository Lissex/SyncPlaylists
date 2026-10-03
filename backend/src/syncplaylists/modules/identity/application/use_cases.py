import hashlib
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

from syncplaylists.modules.identity.application.dto import AuthResult, UserDto
from syncplaylists.modules.identity.application.ports import (
    AttemptLimiter,
    PasswordHasher,
    SessionTokenService,
    UserRepository,
)
from syncplaylists.modules.identity.domain.entities import User
from syncplaylists.modules.identity.domain.errors import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidEmailError,
    TooManyAttemptsError,
)
from syncplaylists.modules.identity.domain.value_objects import Email
from syncplaylists.shared_kernel.application.ports import UnitOfWork

MIN_PASSWORD_LENGTH: Final = 8
MAX_PASSWORD_LENGTH: Final = 128


class WeakPasswordError(ValueError):
    pass


def _attempt_key(action: str, client_ip: str, email: str) -> str:
    # Сырой email в Redis не пишем — только хеш нормализованного значения.
    email_hash = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return f"auth:{action}:{client_ip}:{email_hash}"


async def _check_rate_limit(
    limiter: AttemptLimiter, action: str, client_ip: str, email: str
) -> None:
    retry_after = await limiter.hit(_attempt_key(action, client_ip, email))
    if retry_after is not None:
        raise TooManyAttemptsError(retry_after)


class RegisterUserUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserRepository,
        hasher: PasswordHasher,
        sessions: SessionTokenService,
        limiter: AttemptLimiter,
    ) -> None:
        self._uow = uow
        self._users = users
        self._hasher = hasher
        self._sessions = sessions
        self._limiter = limiter

    async def execute(self, email: str, password: str, client_ip: str) -> AuthResult:
        await _check_rate_limit(self._limiter, "register", client_ip, email)
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise WeakPasswordError(
                f"Пароль должен быть от {MIN_PASSWORD_LENGTH} до {MAX_PASSWORD_LENGTH} символов"
            )
        normalized = Email(email)
        async with self._uow as uow:
            if await self._users.get_by_email(normalized) is not None:
                raise EmailAlreadyRegisteredError(normalized.value)
            user = User.register(
                uuid4(), normalized, self._hasher.hash(password), datetime.now(UTC)
            )
            await self._users.add(user)
            uow.track(user)
            await uow.commit()
        token = await self._sessions.issue(user.id)
        return AuthResult(user=UserDto.from_domain(user), session_token=token)


class LoginUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserRepository,
        hasher: PasswordHasher,
        sessions: SessionTokenService,
        limiter: AttemptLimiter,
    ) -> None:
        self._uow = uow
        self._users = users
        self._hasher = hasher
        self._sessions = sessions
        self._limiter = limiter
        # Хеш-пустышка: для неизвестного email всё равно тратим время на verify —
        # иначе по времени ответа видно, зарегистрирован ли адрес.
        self._dummy_hash = hasher.hash("dummy-password-for-timing")

    async def execute(self, email: str, password: str, client_ip: str) -> AuthResult:
        await _check_rate_limit(self._limiter, "login", client_ip, email)
        try:
            normalized: Email | None = Email(email)
        except InvalidEmailError:
            normalized = None

        async with self._uow as uow:
            user = await self._users.get_by_email(normalized) if normalized else None
            if user is None:
                self._hasher.verify(self._dummy_hash, password)
                raise InvalidCredentialsError
            if not self._hasher.verify(user.password_hash, password):
                raise InvalidCredentialsError
            if self._hasher.needs_rehash(user.password_hash):
                user.change_password_hash(self._hasher.hash(password))
                await self._users.save(user)
                await uow.commit()

        token = await self._sessions.issue(user.id)
        return AuthResult(user=UserDto.from_domain(user), session_token=token)


class ResolveSessionUseCase:
    def __init__(self, users: UserRepository, sessions: SessionTokenService) -> None:
        self._users = users
        self._sessions = sessions

    async def execute(self, token: str) -> UUID | None:
        user_id = await self._sessions.resolve(token)
        if user_id is None:
            return None
        if await self._users.get(user_id) is None:
            # Пользователя удалили, а сессия ещё жива — гасим её.
            await self._sessions.revoke(token)
            return None
        return user_id


class LogoutUseCase:
    def __init__(self, sessions: SessionTokenService) -> None:
        self._sessions = sessions

    async def execute(self, token: str) -> None:
        await self._sessions.revoke(token)


class LogoutAllUseCase:
    def __init__(self, sessions: SessionTokenService) -> None:
        self._sessions = sessions

    async def execute(self, user_id: UUID) -> None:
        await self._sessions.revoke_all(user_id)


class GetCurrentUserUseCase:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def execute(self, user_id: UUID) -> UserDto | None:
        user = await self._users.get(user_id)
        return UserDto.from_domain(user) if user is not None else None

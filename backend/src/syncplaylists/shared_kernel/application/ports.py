from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from syncplaylists.shared_kernel.domain.base import AggregateRoot
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport


class UnitOfWork(Protocol):
    """__aexit__ — только защитный rollback при исключении; обычный выход из
    `async with` НИЧЕГО не коммитит сам. commit() нужно звать явно — забытый commit
    молча ничего не сохранит, а не молча всё закоммитит."""

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def track(self, aggregate: AggregateRoot) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class EventPublisher(Protocol):
    async def publish(self, topic: str, payload: Mapping[str, Any]) -> None: ...


class TaskQueue(Protocol):
    async def enqueue(self, task_name: str, *args: Any, **kwargs: Any) -> None: ...

    # Отложенная задача: выполнится не раньше `when`. `dedupe_key` — одна задача на ключ
    # (повторная постановка с тем же ключом ничего не добавляет).
    async def enqueue_at(
        self, task_name: str, when: datetime, *args: Any, dedupe_key: str | None = None
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PlatformCredentials:
    """Расшифрованные токены площадки. Живут только в памяти процесса, который
    обращается к площадке: в ARQ-задачи, события и логи передаётся только account_id,
    AccountAccess резолвится заново внутри воркера. repr=False — чтобы токены не
    утекли в лог через случайный repr()/f-строку."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccountAccess:
    """Собранный вызывающей стороной VO «доступ к площадке от имени аккаунта».
    shared_kernel не импортирует modules.accounts.domain.ConnectedAccount — это была
    бы обратная зависимость; accounts сам собирает этот VO (AccountAccessProvider)."""

    account_id: UUID
    user_id: UUID
    platform: Platform
    transport: Transport
    # id аккаунта на площадке (проверен через профиль площадки при подключении) —
    # по нему адаптер/use case узнаёт «свой» плейлист.
    external_user_id: str
    credentials: PlatformCredentials


class AccountNotAvailableError(Exception):
    """Аккаунта нет, он чужой, отключён/истёк или подключён к другой площадке."""


class AccountAccessProvider(Protocol):
    """Публичный канал контекста accounts для остальных контекстов (transfers,
    адаптеры площадок). Все методы бросают AccountNotAvailableError."""

    async def get(self, user_id: UUID, account_id: UUID) -> AccountAccess: ...

    # Один активный аккаунт на (user, platform) — см. ARCHITECTURE.md, раздел 11b.
    async def for_platform(self, user_id: UUID, platform: Platform) -> AccountAccess: ...

    # Адаптеры сохраняют обновлённые OAuth-токены (после refresh). Пишется в ОТДЕЛЬНОЙ
    # транзакции: если бы запись шла в UoW переноса и он откатился, новый refresh_token
    # потерялся бы, а старый площадка при ротации уже инвалидировала.
    async def update_credentials(
        self, account_id: UUID, credentials: PlatformCredentials
    ) -> None: ...

    # Площадка ответила PlatformAuthError. Токен перепроверяется через профиль
    # площадки ещё раз: True — токен действительно не принят, аккаунт переведён в
    # EXPIRED; False — профиль ответил нормально, ошибка была разовой (повторить запрос).
    async def report_auth_failure(self, account_id: UUID) -> bool: ...


CredentialsRenewal = Callable[[PlatformCredentials], Awaitable[PlatformCredentials]]


class CredentialsRefresher(Protocol):
    """Обновление токенов аккаунта (OAuth refresh) без гонок между воркерами.

    `renew(current)` вызывается под блокировкой строки аккаунта, с актуальными (только
    что прочитанными) токенами: если параллельный воркер уже обновил их — `stale` не
    совпадёт с текущими, и они вернутся без нового запроса к площадке. Это важно при
    ротации refresh_token: второй refresh со старым токеном площадка бы отвергла.
    Новые токены сохраняются в отдельной транзакции (см. update_credentials).
    Бросает AccountNotAvailableError; ошибки renew (PlatformAuthError — refresh_token
    отозван) пробрасываются, ничего не сохраняя."""

    async def refresh(
        self, account_id: UUID, stale: PlatformCredentials, renew: CredentialsRenewal
    ) -> PlatformCredentials: ...


class GatewayFactory(Protocol):
    # Выбирает реализацию шлюза по access.platform/access.transport
    # (official / unofficial / extension) и передаёт ей credentials.
    # Бросает PlatformNotSupportedError, если адаптера нет.
    def for_account(self, access: AccountAccess) -> MusicPlatformGateway: ...

    def supports(self, platform: Platform) -> bool: ...


class PlatformRateLimiter(Protocol):
    """Token bucket на (площадка, аккаунт): адаптер зовёт acquire() перед каждым
    HTTP-запросом. Ждёт свободный токен; если ждать дольше допустимого — бросает
    PlatformRateLimitedError (задача уйдёт в повтор с задержкой)."""

    async def acquire(self, platform: Platform, account_id: UUID) -> None: ...

    # Площадка сама ответила 429 с Retry-After: пауза для ВСЕГО аккаунта, а не только
    # для задачи, которая её получила, — иначе остальные параллельные задачи продолжат
    # стучаться и каждая соберёт свой 429.
    async def penalize(self, platform: Platform, account_id: UUID, seconds: float) -> None: ...

    # Учёт КАЖДОГО HTTP-запроса к площадке с этого сервера (по всем аккаунтам, включая
    # проверку профиля без аккаунта) — чтобы понять, квота площадки на токен или на IP.
    async def count_request(self, platform: Platform) -> None: ...

    # Сколько запросов за последние `minutes` минут: account_id — выданных этому аккаунту
    # через acquire(); None — всех запросов с этого сервера (count_request). Для логов при
    # 429 и подбора лимита под реальную квоту площадки.
    async def recent_requests(
        self, platform: Platform, account_id: UUID | None, minutes: int
    ) -> int: ...

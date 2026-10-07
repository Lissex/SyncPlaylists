from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from syncplaylists.modules.extension.domain.entities import ExtensionDevice
from syncplaylists.modules.extension.domain.value_objects import SessionState
from syncplaylists.shared_kernel.domain.value_objects import Platform


class ExtensionDeviceRepository(Protocol):
    async def add(self, device: ExtensionDevice) -> None: ...

    async def get(self, device_id: UUID) -> ExtensionDevice | None: ...

    async def find_by_token_hash(self, token_hash: str) -> ExtensionDevice | None: ...

    async def list_for_user(self, user_id: UUID) -> list[ExtensionDevice]: ...

    async def save(self, device: ExtensionDevice) -> None: ...


@dataclass(frozen=True, slots=True)
class PendingPairing:
    """Привязка в процессе: расширение показало код, пользователь его ещё не ввёл
    (user_id None) или уже подтвердил на сайте (user_id задан)."""

    device_name: str
    browser: str
    version: str
    user_id: UUID | None = None


class PairingStore(Protocol):
    """Короткоживущие привязки (Redis, TTL = срок кода). pairing_id знает только
    расширение (по нему оно забирает токен), код — человек (вводит его на сайте); в
    хранилище оба лежат только как sha256."""

    # False — такой код уже занят другой привязкой (вызывающий сгенерирует новый).
    async def create(
        self, pairing_id_hash: str, code_hash: str, pairing: PendingPairing, ttl_seconds: int
    ) -> bool: ...

    # Атомарно: код одноразовый. False — кода нет/истёк/уже использован.
    async def confirm(self, code_hash: str, user_id: UUID) -> bool: ...

    async def get(self, pairing_id_hash: str) -> PendingPairing | None: ...

    # Подтверждённая привязка забирается один раз (дальше — только токен устройства).
    async def take(self, pairing_id_hash: str) -> PendingPairing | None: ...


class AttemptLimiter(Protocol):
    # Засчитывает попытку по ключу; через сколько секунд можно повторить, если лимит
    # окна превышен, иначе None.
    async def hit(self, key: str) -> int | None: ...


@dataclass(frozen=True, slots=True)
class PlatformPresence:
    """Что расширение сообщило о площадке: можно ли там сейчас выполнять задачи и под
    каким аккаунтом площадки открыта сессия в браузере."""

    platform: Platform
    session: SessionState
    external_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExtensionCall:
    """Операция на площадке, которую выполнит расширение в браузере пользователя.
    `operation` — имя из реестра операций (operations.py), `items` — сколько треков в
    операции (от этого растёт таймаут записи), `idempotency_key` — для записей, повтор
    которых недопустим (создание плейлиста)."""

    user_id: UUID
    platform: Platform
    external_user_id: str
    operation: str
    args: Mapping[str, Any] = field(default_factory=dict)
    items: int = 0
    idempotency_key: str | None = None


class ExtensionChannel(Protocol):
    """Сторона воркера: отдать операцию расширению пользователя и дождаться результата.

    Бросает ExtensionUnavailableError (нет подходящего расширения в сети, таймаут, нет
    входа/разрешения/капча) и ошибки площадки из shared_kernel.domain.errors, о которых
    сообщило расширение. Повтор с тем же idempotency_key, если операция уже выполнена,
    возвращает сохранённый результат и в браузер не уходит."""

    async def call(self, call: ExtensionCall) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class DeliveredTask:
    task_id: str
    op: str  # "<platform>.<operation>"
    args: Mapping[str, Any]
    deadline: float  # unix-время, после которого результат уже никто не ждёт
    idempotency_key: str | None = None


class ExtensionHub(Protocol):
    """Сторона WebSocket (процесс api): присутствие расширений и выдача им задач."""

    async def publish_presence(
        self,
        user_id: UUID,
        device_id: UUID,
        presence: Sequence[PlatformPresence],
        ttl_seconds: int,
    ) -> None: ...

    async def drop_presence(
        self, user_id: UUID, device_id: UUID, platforms: Sequence[Platform]
    ) -> None: ...

    # Задачи, выданные устройству до обрыва связи и не завершённые, — снова в начало
    # очереди (расширение не выполнит запись повторно: журнал по idempotency_key, а
    # запись треков идемпотентна — уже добавленное не добавляется).
    async def requeue_inflight(self, device_id: UUID) -> None: ...

    async def next_task(self, device_id: UUID, wait_seconds: float) -> DeliveredTask | None: ...

    # Результат задачи. Принимается только от того устройства, которому задачу выдали.
    # False — задача неизвестна (давно истекла) или чужая.
    async def complete(self, device_id: UUID, task_id: str, outcome: Mapping[str, Any]) -> bool: ...

    # Расширение сообщает, что задача ещё выполняется: дедлайн отодвигается.
    async def extend(self, device_id: UUID, task_id: str) -> None: ...

    # Устройство на связи (WebSocket жив) — независимо от площадок; ставится при
    # подключении и каждом ping, снимается при обрыве.
    async def mark_online(self, user_id: UUID, device_id: UUID, ttl_seconds: int) -> None: ...

    async def drop_online(self, device_id: UUID) -> None: ...


class DeviceCaller(Protocol):
    """Операция на конкретном устройстве, без площадки (диагностика связи). Возвращает
    исход как прислало расширение ({ok, data} | {ok: false, error}); бросает
    DeviceUnavailableError (устройство не на связи / не ответило вовремя)."""

    async def call_device(
        self,
        user_id: UUID,
        device_id: UUID,
        operation: str,
        args: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ProbeRecord:
    """Проверка связи с устройством: pending, затем ok (data) или error (code)."""

    user_id: UUID
    device_id: UUID
    status: Literal["pending", "ok", "error"]
    data: Mapping[str, Any] | None = None
    error: str | None = None


class ProbeStore(Protocol):
    async def save(self, probe_id: str, record: ProbeRecord, ttl_seconds: int) -> None: ...

    async def get(self, probe_id: str) -> ProbeRecord | None: ...

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from syncplaylists.modules.accounts.application.dto import AccountDto
from syncplaylists.modules.accounts.application.use_cases import ConnectAccountUseCase
from syncplaylists.modules.extension.application.dto import (
    DeviceDto,
    PairingResult,
    PairingStarted,
)
from syncplaylists.modules.extension.application.ports import (
    ExtensionDeviceRepository,
    ExtensionHub,
    PairingStore,
    PendingPairing,
)
from syncplaylists.modules.extension.domain.entities import ExtensionDevice
from syncplaylists.modules.extension.domain.errors import (
    DeviceNotFoundError,
    PairingNotFoundError,
    PlatformAccountConflictError,
)
from syncplaylists.modules.extension.domain.value_objects import (
    generate_device_token,
    generate_pairing_code,
    hash_secret,
    normalize_pairing_code,
)
from syncplaylists.shared_kernel.application.ports import (
    AccountAccessProvider,
    AccountNotAvailableError,
    UnitOfWork,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform

_CODE_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class PairingConfig:
    code_ttl_seconds: int
    poll_interval_seconds: int


class StartPairingUseCase:
    """Шаг 1 привязки (как device flow): расширение получает код для человека и
    секретный pairing_id для себя. Код человек вводит на сайте, где он уже вошёл, —
    так расширение привязывается к тому, кто залогинен на сайте, а не к тому, чей код
    ему подсунули."""

    def __init__(self, store: PairingStore, config: PairingConfig) -> None:
        self._store = store
        self._config = config

    async def execute(self, *, device_name: str, browser: str, version: str) -> PairingStarted:
        pairing_id = secrets.token_urlsafe(24)
        pending = PendingPairing(device_name=device_name, browser=browser, version=version)
        for _ in range(_CODE_ATTEMPTS):
            code = generate_pairing_code()
            if await self._store.create(
                hash_secret(pairing_id), hash_secret(code), pending, self._config.code_ttl_seconds
            ):
                return PairingStarted(
                    pairing_id=pairing_id,
                    user_code=code,
                    expires_in=self._config.code_ttl_seconds,
                    interval=self._config.poll_interval_seconds,
                )
        raise RuntimeError("Не удалось выдать уникальный код привязки")


class ConfirmPairingUseCase:
    """Шаг 2: залогиненный пользователь вводит код на сайте. Код одноразовый."""

    def __init__(self, store: PairingStore) -> None:
        self._store = store

    async def execute(self, user_id: UUID, user_code: str) -> None:
        code_hash = hash_secret(normalize_pairing_code(user_code))
        if not await self._store.confirm(code_hash, user_id):
            raise PairingNotFoundError("Код неверный или истёк")


class ClaimPairingUseCase:
    """Шаг 3: расширение опрашивает по pairing_id. Пока код не подтверждён — None;
    после подтверждения — один раз выдаётся токен устройства (в БД — только sha256)."""

    def __init__(
        self, uow: UnitOfWork, store: PairingStore, devices: ExtensionDeviceRepository
    ) -> None:
        self._uow = uow
        self._store = store
        self._devices = devices

    async def execute(self, pairing_id: str) -> PairingResult | None:
        pairing_id_hash = hash_secret(pairing_id)
        pending = await self._store.get(pairing_id_hash)
        if pending is None:
            raise PairingNotFoundError("Привязка истекла — начните заново")
        if pending.user_id is None:
            return None
        taken = await self._store.take(pairing_id_hash)
        if taken is None or taken.user_id is None:
            raise PairingNotFoundError("Привязка уже завершена")
        token = generate_device_token()
        device = ExtensionDevice.pair(
            device_id=uuid4(),
            user_id=taken.user_id,
            name=taken.device_name,
            browser=taken.browser,
            version=taken.version,
            token_hash=hash_secret(token),
            now=datetime.now(UTC),
        )
        async with self._uow as uow:
            await self._devices.add(device)
            uow.track(device)
            await uow.commit()
        return PairingResult(device_id=device.id, device_token=token)


class AuthenticateDeviceUseCase:
    """Токен устройства → устройство, если оно не отозвано и токен не просрочен.
    `touch` — отметить «последний раз на связи» (при подключении WebSocket)."""

    def __init__(
        self, uow: UnitOfWork, devices: ExtensionDeviceRepository, token_ttl: timedelta
    ) -> None:
        self._uow = uow
        self._devices = devices
        self._token_ttl = token_ttl

    async def execute(
        self, token: str, *, touch: bool = False, version: str | None = None
    ) -> DeviceDto | None:
        if not token:
            return None
        now = datetime.now(UTC)
        async with self._uow as uow:
            device = await self._devices.find_by_token_hash(hash_secret(token))
            if device is None or not device.is_usable(now, self._token_ttl):
                return None
            if touch:
                device.touch(now, version)
                await self._devices.save(device)
                await uow.commit()
        return DeviceDto.from_domain(device)


class ListDevicesUseCase:
    def __init__(self, devices: ExtensionDeviceRepository) -> None:
        self._devices = devices

    async def execute(self, user_id: UUID) -> list[DeviceDto]:
        return [DeviceDto.from_domain(d) for d in await self._devices.list_for_user(user_id)]


class RevokeDeviceUseCase:
    """Отзыв устройства с сайта. Задачи ему больше не выдаются сразу (присутствие
    снимается), а открытый WebSocket закроется на ближайшей перепроверке токена."""

    def __init__(
        self, uow: UnitOfWork, devices: ExtensionDeviceRepository, hub: ExtensionHub
    ) -> None:
        self._uow = uow
        self._devices = devices
        self._hub = hub

    async def execute(self, user_id: UUID, device_id: UUID) -> None:
        async with self._uow as uow:
            device = await self._devices.get(device_id)
            if device is None or device.user_id != user_id:
                raise DeviceNotFoundError(str(device_id))
            device.revoke(datetime.now(UTC))
            await self._devices.save(device)
            uow.track(device)
            await uow.commit()
        await self._hub.drop_presence(user_id, device_id, list(Platform))
        await self._hub.drop_online(device_id)


class ConnectPlatformViaExtensionUseCase:
    """Пользователь в расширении явно нажал «Подключить <площадку>»: аккаунт площадки
    подключается с транспортом EXTENSION (без токенов) — через публичный use case
    контекста accounts."""

    def __init__(
        self, connect_account: ConnectAccountUseCase, accounts: AccountAccessProvider
    ) -> None:
        self._connect_account = connect_account
        self._accounts = accounts

    async def execute(
        self, user_id: UUID, platform: Platform, external_user_id: str, display_name: str | None
    ) -> AccountDto:
        """Бросает PlatformAccountConflictError: на площадке уже подключён другой аккаунт."""
        try:
            active = await self._accounts.for_platform(user_id, platform)
        except AccountNotAvailableError:
            active = None
        if active is not None and active.external_user_id != external_user_id:
            raise PlatformAccountConflictError(platform.value)
        return await self._connect_account.connect_via_extension(
            user_id=user_id,
            platform=platform,
            external_user_id=external_user_id,
            display_name=display_name,
        )

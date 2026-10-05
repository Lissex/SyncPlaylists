"""Привязка расширения одноразовым кодом (как device flow): код показывает расширение,
вводит залогиненный пользователь на сайте, токен устройства выдаётся один раз."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from syncplaylists.modules.extension.application.use_cases import (
    AuthenticateDeviceUseCase,
    ClaimPairingUseCase,
    ConfirmPairingUseCase,
    PairingConfig,
    RevokeDeviceUseCase,
    StartPairingUseCase,
)
from syncplaylists.modules.extension.domain.errors import DeviceNotFoundError, PairingNotFoundError
from syncplaylists.modules.extension.domain.value_objects import (
    hash_secret,
    normalize_pairing_code,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform
from tests.fakes import FakeUnitOfWork
from tests.fakes.extension import InMemoryDeviceRepository, InMemoryPairingStore, RecordingHub

_TTL = timedelta(days=180)


class _Env:
    def __init__(self) -> None:
        self.store = InMemoryPairingStore()
        self.devices = InMemoryDeviceRepository()
        self.uow = FakeUnitOfWork()
        self.hub = RecordingHub()
        self.user_id = uuid4()

    def start(self) -> StartPairingUseCase:
        return StartPairingUseCase(self.store, PairingConfig(300, 3))

    def confirm(self) -> ConfirmPairingUseCase:
        return ConfirmPairingUseCase(self.store)

    def claim(self) -> ClaimPairingUseCase:
        return ClaimPairingUseCase(self.uow, self.store, self.devices)

    def authenticate(self) -> AuthenticateDeviceUseCase:
        return AuthenticateDeviceUseCase(self.uow, self.devices, _TTL)


async def _start(env: _Env) -> tuple[str, str]:
    started = await env.start().execute(device_name="Chrome", browser="chrome", version="0.1.0")
    return started.pairing_id, started.user_code


async def test_full_pairing_gives_device_token_once() -> None:
    env = _Env()
    pairing_id, code = await _start(env)

    assert await env.claim().execute(pairing_id) is None  # код ещё не введён
    await env.confirm().execute(env.user_id, code)
    result = await env.claim().execute(pairing_id)

    assert result is not None
    device = env.devices.devices[result.device_id]
    assert device.user_id == env.user_id
    assert device.token_hash == hash_secret(result.device_token)  # сам токен не хранится
    assert result.device_token not in repr(result)
    with pytest.raises(PairingNotFoundError):
        await env.claim().execute(pairing_id)  # второй раз токен не выдаётся


async def test_secrets_are_stored_only_as_hashes() -> None:
    env = _Env()
    pairing_id, code = await _start(env)

    assert pairing_id not in env.store.pairings
    assert hash_secret(pairing_id) in env.store.pairings
    assert code not in env.store.codes
    assert hash_secret(code) in env.store.codes


async def test_code_is_single_use_and_case_insensitive() -> None:
    env = _Env()
    _, code = await _start(env)

    await env.confirm().execute(env.user_id, code.lower().replace("-", " "))
    with pytest.raises(PairingNotFoundError):
        await env.confirm().execute(uuid4(), code)  # чужой не перехватит уже введённый код


async def test_wrong_code_is_rejected() -> None:
    env = _Env()
    await _start(env)

    with pytest.raises(PairingNotFoundError):
        await env.confirm().execute(env.user_id, "AAAA-AAAA")


async def test_unknown_pairing_is_gone() -> None:
    env = _Env()
    with pytest.raises(PairingNotFoundError):
        await env.claim().execute("no-such-pairing-id")


def test_code_normalization() -> None:
    assert normalize_pairing_code(" abcd efgh ") == "ABCD-EFGH"
    assert normalize_pairing_code("ABCD-EFGH") == "ABCD-EFGH"


async def _paired(env: _Env) -> tuple[str, str]:
    pairing_id, code = await _start(env)
    await env.confirm().execute(env.user_id, code)
    result = await env.claim().execute(pairing_id)
    assert result is not None
    return str(result.device_id), result.device_token


async def test_authenticate_touches_device() -> None:
    env = _Env()
    _, token = await _paired(env)

    device = await env.authenticate().execute(token, touch=True, version="0.2.0")

    assert device is not None
    assert device.user_id == env.user_id
    assert device.version == "0.2.0"
    assert device.last_seen_at is not None
    assert await env.authenticate().execute("wrong-token") is None
    assert await env.authenticate().execute("") is None


async def test_revoked_device_is_not_authenticated_and_loses_presence() -> None:
    env = _Env()
    device_id, token = await _paired(env)
    device = next(iter(env.devices.devices.values()))

    await RevokeDeviceUseCase(env.uow, env.devices, env.hub).execute(env.user_id, device.id)

    assert await env.authenticate().execute(token) is None
    assert env.hub.dropped == [(env.user_id, device.id, list(Platform))]
    assert str(device.id) == device_id


async def test_revoke_foreign_device_is_not_found() -> None:
    env = _Env()
    await _paired(env)
    device = next(iter(env.devices.devices.values()))

    with pytest.raises(DeviceNotFoundError):
        await RevokeDeviceUseCase(env.uow, env.devices, env.hub).execute(uuid4(), device.id)


async def test_expired_device_token_is_not_authenticated() -> None:
    env = _Env()
    _, token = await _paired(env)
    device = next(iter(env.devices.devices.values()))
    device.created_at = datetime.now(UTC) - _TTL - timedelta(seconds=1)

    assert await env.authenticate().execute(token) is None

"""Аккаунт площадки через браузерное расширение (транспорт EXTENSION): токенов на
сервере нет, доступ отдаётся без credentials, «протухнуть» такой аккаунт не может."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.modules.accounts.domain.errors import InvalidAccountTransitionError
from syncplaylists.modules.accounts.domain.value_objects import AccountStatus, EncryptedToken
from syncplaylists.shared_kernel.domain.errors import PlatformNotSupportedError
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport
from tests.unit.accounts.test_use_cases import Env

_NOW = datetime(2026, 10, 5, tzinfo=UTC)


async def test_connect_via_extension_stores_no_tokens() -> None:
    env = Env()

    dto = await env.connect_use_case().connect_via_extension(
        user_id=env.user_id,
        platform=Platform.SOUNDCLOUD,
        external_user_id="sc-42",
        display_name="Me",
    )

    account = await env.accounts.get(dto.id)
    assert account is not None
    assert account.transport is Transport.EXTENSION
    assert (account.access_token, account.refresh_token) == (None, None)
    assert account.status is AccountStatus.ACTIVE


async def test_access_for_extension_account_has_no_credentials() -> None:
    env = Env()
    dto = await env.connect_use_case().connect_via_extension(
        user_id=env.user_id,
        platform=Platform.SOUNDCLOUD,
        external_user_id="sc-42",
        display_name=None,
    )

    access = await env.access().for_platform(env.user_id, Platform.SOUNDCLOUD)

    assert access.account_id == dto.id
    assert access.transport is Transport.EXTENSION
    assert access.credentials is None
    with pytest.raises(PlatformNotSupportedError):
        access.require_credentials()  # серверный адаптер такой аккаунт не обслужит
    # «Токен протух» через расширение не бывает — аккаунт не уходит в EXPIRED.
    assert await env.access().report_auth_failure(dto.id) is False


async def test_token_account_switched_to_extension_drops_tokens() -> None:
    env = Env()
    account_id = await env.connect(platform=Platform.VK, external_user_id="vk-1")

    await env.connect_use_case().connect_via_extension(
        user_id=env.user_id, platform=Platform.VK, external_user_id="vk-1", display_name=None
    )

    account = await env.accounts.get(account_id)
    assert account is not None
    assert account.transport is Transport.EXTENSION
    assert account.access_token is None


def test_extension_account_cannot_hold_tokens() -> None:
    with pytest.raises(ValueError, match="не хранит токены"):
        ConnectedAccount.connect(
            account_id=uuid4(),
            user_id=uuid4(),
            platform=Platform.VK,
            transport=Transport.EXTENSION,
            external_user_id="vk-1",
            display_name=None,
            access_token=EncryptedToken(b"x"),
            refresh_token=None,
            expires_at=None,
            now=_NOW,
        )


def test_token_transport_requires_token() -> None:
    with pytest.raises(ValueError, match="нужен токен"):
        ConnectedAccount.connect(
            account_id=uuid4(),
            user_id=uuid4(),
            platform=Platform.VK,
            transport=Transport.UNOFFICIAL,
            external_user_id="vk-1",
            display_name=None,
            access_token=None,
            refresh_token=None,
            expires_at=None,
            now=_NOW,
        )


async def test_extension_account_credentials_cannot_be_refreshed() -> None:
    env = Env()
    dto = await env.connect_use_case().connect_via_extension(
        user_id=env.user_id, platform=Platform.VK, external_user_id="vk-1", display_name=None
    )
    account = await env.accounts.get(dto.id)
    assert account is not None

    with pytest.raises(InvalidAccountTransitionError):
        account.refresh_credentials(EncryptedToken(b"x"), None, None)

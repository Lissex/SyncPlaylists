from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.modules.accounts.domain.errors import (
    AccountNotUsableError,
    InvalidAccountTransitionError,
)
from syncplaylists.modules.accounts.domain.events import AccountConnected, AccountDisconnected
from syncplaylists.modules.accounts.domain.value_objects import AccountStatus, EncryptedToken
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport

_NOW = datetime(2026, 10, 3, tzinfo=UTC)


def _account() -> ConnectedAccount:
    return ConnectedAccount.connect(
        account_id=uuid4(),
        user_id=uuid4(),
        platform=Platform.VK,
        transport=Transport.UNOFFICIAL,
        external_user_id="vk-1",
        display_name="Alice",
        access_token=EncryptedToken(b"access-1"),
        refresh_token=EncryptedToken(b"refresh-1"),
        expires_at=None,
        now=_NOW,
    )


def test_connect_is_active_and_records_event() -> None:
    account = _account()

    assert account.status is AccountStatus.ACTIVE
    assert account.ensure_usable() == EncryptedToken(b"access-1")
    assert [type(e) for e in account.pull_domain_events()] == [AccountConnected]


def test_connect_requires_external_user_id() -> None:
    with pytest.raises(ValueError, match="external_user_id"):
        ConnectedAccount.connect(
            account_id=uuid4(),
            user_id=uuid4(),
            platform=Platform.VK,
            transport=Transport.UNOFFICIAL,
            external_user_id="",
            display_name=None,
            access_token=EncryptedToken(b"x"),
            refresh_token=None,
            expires_at=None,
            now=_NOW,
        )


def test_disconnect_wipes_tokens_and_makes_account_unusable() -> None:
    account = _account()
    account.pull_domain_events()

    account.disconnect(_NOW)

    assert account.status is AccountStatus.DISCONNECTED
    assert account.access_token is None
    assert account.refresh_token is None
    assert [type(e) for e in account.pull_domain_events()] == [AccountDisconnected]
    with pytest.raises(AccountNotUsableError):
        account.ensure_usable()


def test_disconnect_is_idempotent() -> None:
    account = _account()
    account.disconnect(_NOW)
    account.pull_domain_events()

    account.disconnect(_NOW)

    assert account.pull_domain_events() == []


def test_reconnect_reactivates_with_new_tokens() -> None:
    account = _account()
    account.disconnect(_NOW)

    account.reconnect(
        transport=Transport.OFFICIAL,
        display_name=None,
        access_token=EncryptedToken(b"access-2"),
        refresh_token=None,
        expires_at=None,
        now=_NOW,
    )

    assert account.status is AccountStatus.ACTIVE
    assert account.transport is Transport.OFFICIAL
    assert account.display_name == "Alice"  # None не затирает имя
    assert account.ensure_usable() == EncryptedToken(b"access-2")


def test_refresh_credentials_keeps_old_refresh_token_if_not_rotated() -> None:
    account = _account()
    account.mark_expired()

    account.refresh_credentials(EncryptedToken(b"access-2"), None, None)

    assert account.status is AccountStatus.ACTIVE
    assert account.access_token == EncryptedToken(b"access-2")
    assert account.refresh_token == EncryptedToken(b"refresh-1")


def test_refresh_credentials_cannot_revive_disconnected_account() -> None:
    account = _account()
    account.disconnect(_NOW)

    with pytest.raises(InvalidAccountTransitionError):
        account.refresh_credentials(EncryptedToken(b"access-2"), None, None)


def test_expired_account_is_not_usable() -> None:
    account = _account()
    account.mark_expired()

    with pytest.raises(AccountNotUsableError):
        account.ensure_usable()


def test_encrypted_token_repr_hides_ciphertext() -> None:
    assert "secret" not in repr(EncryptedToken(b"secret"))

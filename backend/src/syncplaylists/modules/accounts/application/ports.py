from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.shared_kernel.domain.value_objects import Platform


class ConnectedAccountRepository(Protocol):
    async def get(self, account_id: UUID) -> ConnectedAccount | None: ...

    async def find_by_external(
        self, user_id: UUID, platform: Platform, external_user_id: str
    ) -> ConnectedAccount | None: ...

    async def find_active(self, user_id: UUID, platform: Platform) -> ConnectedAccount | None: ...

    async def list_for_user(self, user_id: UUID) -> list[ConnectedAccount]: ...

    # Бросает AccountAlreadyConnectedError при гонке за «один активный на площадку».
    async def add(self, account: ConnectedAccount) -> None: ...

    async def save(self, account: ConnectedAccount) -> None: ...


class AccountCredentialsWriter(Protocol):
    """Изменение аккаунта в СОБСТВЕННОЙ транзакции, независимой от UoW вызывающего
    use case: загрузить под локом → change(account) → сохранить → commit. Нужен для
    сохранения обновлённых OAuth-токенов, которые нельзя потерять при откате переноса.

    Вызывающая транзакция НЕ должна сама держать лок на этой строке connected_accounts
    (UPDATE/FOR UPDATE без commit) — иначе writer будет ждать её лока вечно."""

    async def apply(
        self, account_id: UUID, change: Callable[[ConnectedAccount], None]
    ) -> bool: ...  # False — аккаунта нет


class TokenCipher(Protocol):
    # aad (associated data) привязывает шифротекст к месту хранения — см. token_aad().
    def encrypt(self, plaintext: str, *, aad: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes, *, aad: bytes) -> str: ...


@dataclass(frozen=True, slots=True)
class OAuthGrant:
    external_user_id: str
    display_name: str | None
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None


class OAuthProvider(Protocol):
    platform: Platform

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    # Обмен кода на токены + профиль (external_user_id/display_name) аккаунта площадки.
    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthGrant: ...


class OAuthProviderRegistry(Protocol):
    def get(self, platform: Platform) -> OAuthProvider | None: ...


@dataclass(frozen=True, slots=True)
class PendingOAuth:
    user_id: UUID
    platform: Platform
    code_verifier: str = field(repr=False)


class OAuthStateStore(Protocol):
    async def save(self, state: str, pending: PendingOAuth, ttl_seconds: int) -> None: ...

    # Атомарно читает и удаляет: state одноразовый.
    async def pop(self, state: str) -> PendingOAuth | None: ...

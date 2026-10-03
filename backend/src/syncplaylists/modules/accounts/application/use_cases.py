import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from syncplaylists.modules.accounts.application.dto import AccountDto
from syncplaylists.modules.accounts.application.ports import (
    ConnectedAccountRepository,
    OAuthProviderRegistry,
    OAuthStateStore,
    PendingOAuth,
    PlatformProfileRegistry,
    TokenCipher,
)
from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.modules.accounts.domain.errors import (
    AccountAlreadyConnectedError,
    AccountNotFoundError,
    InvalidPlatformTokenError,
)
from syncplaylists.modules.accounts.domain.value_objects import EncryptedToken
from syncplaylists.shared_kernel.application.ports import PlatformCredentials, UnitOfWork
from syncplaylists.shared_kernel.domain.errors import PlatformAuthError, PlatformNotSupportedError
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport


def token_aad(account_id: UUID, kind: str) -> bytes:
    """AAD для TokenCipher: шифротекст привязан к аккаунту и полю (access/refresh)."""
    return f"connected_account:{account_id}:{kind}".encode()


def encrypt_credentials(
    cipher: TokenCipher, account_id: UUID, credentials: PlatformCredentials
) -> tuple[EncryptedToken, EncryptedToken | None]:
    access = EncryptedToken(
        cipher.encrypt(credentials.access_token, aad=token_aad(account_id, "access"))
    )
    refresh = (
        EncryptedToken(
            cipher.encrypt(credentials.refresh_token, aad=token_aad(account_id, "refresh"))
        )
        if credentials.refresh_token is not None
        else None
    )
    return access, refresh


class ConnectAccountUseCase:
    """Подключение аккаунта площадки. Ручное подключение по токену (`execute`) сначала
    проверяет токен через профиль площадки и берёт external_user_id/display_name оттуда —
    присланному клиентом id не доверяем. OAuth (`connect_verified`) получает профиль
    уже от OAuthProvider вместе с токенами."""

    def __init__(
        self,
        uow: UnitOfWork,
        accounts: ConnectedAccountRepository,
        cipher: TokenCipher,
        profiles: PlatformProfileRegistry,
    ) -> None:
        self._uow = uow
        self._accounts = accounts
        self._cipher = cipher
        self._profiles = profiles

    async def execute(
        self,
        *,
        user_id: UUID,
        platform: Platform,
        transport: Transport,
        credentials: PlatformCredentials,
    ) -> AccountDto:
        """Бросает InvalidPlatformTokenError (токен не принят), PlatformNotSupportedError
        (для площадки нет проверки профиля), PlatformError (площадка недоступна)."""
        fetcher = self._profiles.get(platform)
        if fetcher is None:
            raise PlatformNotSupportedError(platform)
        try:
            profile = await fetcher.fetch(credentials)
        except PlatformAuthError as exc:
            raise InvalidPlatformTokenError(f"{platform} не принял токен") from exc
        return await self.connect_verified(
            user_id=user_id,
            platform=platform,
            transport=transport,
            external_user_id=profile.external_user_id,
            display_name=profile.display_name,
            credentials=credentials,
        )

    async def connect_verified(
        self,
        *,
        user_id: UUID,
        platform: Platform,
        transport: Transport,
        external_user_id: str,
        display_name: str | None,
        credentials: PlatformCredentials,
    ) -> AccountDto:
        now = datetime.now(UTC)
        async with self._uow as uow:
            account = await self._accounts.find_by_external(user_id, platform, external_user_id)
            active = await self._accounts.find_active(user_id, platform)
            if active is not None and (account is None or active.id != account.id):
                # Один активный аккаунт на площадку (ARCHITECTURE.md, 11b) — сначала
                # отключить текущий.
                raise AccountAlreadyConnectedError(f"На {platform} уже подключён другой аккаунт")

            if account is not None:
                access, refresh = encrypt_credentials(self._cipher, account.id, credentials)
                account.reconnect(
                    transport=transport,
                    display_name=display_name,
                    access_token=access,
                    refresh_token=refresh,
                    expires_at=credentials.expires_at,
                    now=now,
                )
                await self._accounts.save(account)
            else:
                account_id = uuid4()
                access, refresh = encrypt_credentials(self._cipher, account_id, credentials)
                account = ConnectedAccount.connect(
                    account_id=account_id,
                    user_id=user_id,
                    platform=platform,
                    transport=transport,
                    external_user_id=external_user_id,
                    display_name=display_name,
                    access_token=access,
                    refresh_token=refresh,
                    expires_at=credentials.expires_at,
                    now=now,
                )
                await self._accounts.add(account)
            uow.track(account)
            await uow.commit()
        return AccountDto.from_domain(account)


class DisconnectAccountUseCase:
    def __init__(self, uow: UnitOfWork, accounts: ConnectedAccountRepository) -> None:
        self._uow = uow
        self._accounts = accounts

    async def execute(self, user_id: UUID, account_id: UUID) -> None:
        async with self._uow as uow:
            account = await self._accounts.get(account_id)
            if account is None or account.user_id != user_id:
                raise AccountNotFoundError(str(account_id))
            account.disconnect(datetime.now(UTC))
            await self._accounts.save(account)
            uow.track(account)
            await uow.commit()


class ListAccountsUseCase:
    def __init__(self, accounts: ConnectedAccountRepository) -> None:
        self._accounts = accounts

    async def execute(self, user_id: UUID) -> list[AccountDto]:
        return [AccountDto.from_domain(a) for a in await self._accounts.list_for_user(user_id)]


class OAuthProviderNotFoundError(Exception):
    pass


class OAuthFlowError(Exception):
    """state не найден/истёк/выдан другому пользователю или другой площадке."""


@dataclass(frozen=True, slots=True)
class OAuthFlowConfig:
    callback_base_url: str
    state_ttl_seconds: int

    def redirect_uri(self, platform: Platform) -> str:
        return f"{self.callback_base_url.rstrip('/')}/accounts/{platform.value}/oauth/callback"


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class StartOAuthUseCase:
    def __init__(
        self,
        providers: OAuthProviderRegistry,
        states: OAuthStateStore,
        config: OAuthFlowConfig,
    ) -> None:
        self._providers = providers
        self._states = states
        self._config = config

    async def execute(self, user_id: UUID, platform: Platform) -> str:
        provider = self._providers.get(platform)
        if provider is None:
            raise OAuthProviderNotFoundError(platform.value)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)  # 64 символа — в рамках 43..128 из RFC 7636
        await self._states.save(
            state,
            PendingOAuth(user_id=user_id, platform=platform, code_verifier=verifier),
            self._config.state_ttl_seconds,
        )
        return provider.authorization_url(
            state=state,
            code_challenge=_pkce_challenge(verifier),
            redirect_uri=self._config.redirect_uri(platform),
        )


class CompleteOAuthUseCase:
    def __init__(
        self,
        providers: OAuthProviderRegistry,
        states: OAuthStateStore,
        config: OAuthFlowConfig,
        connect_account: ConnectAccountUseCase,
    ) -> None:
        self._providers = providers
        self._states = states
        self._config = config
        self._connect_account = connect_account

    async def execute(self, user_id: UUID, platform: Platform, state: str, code: str) -> AccountDto:
        pending = await self._states.pop(state)
        # state привязан к пользователю, начавшему поток: иначе злоумышленник мог бы
        # подсунуть жертве свой callback и привязать СВОЙ аккаунт площадки к её профилю.
        if pending is None or pending.user_id != user_id or pending.platform is not platform:
            raise OAuthFlowError("Недействительный или истёкший state")
        provider = self._providers.get(platform)
        if provider is None:
            raise OAuthProviderNotFoundError(platform.value)
        grant = await provider.exchange_code(
            code=code,
            code_verifier=pending.code_verifier,
            redirect_uri=self._config.redirect_uri(platform),
        )
        return await self._connect_account.connect_verified(
            user_id=user_id,
            platform=platform,
            transport=Transport.OFFICIAL,
            external_user_id=grant.external_user_id,
            display_name=grant.display_name,
            credentials=PlatformCredentials(
                access_token=grant.access_token,
                refresh_token=grant.refresh_token,
                expires_at=grant.expires_at,
            ),
        )

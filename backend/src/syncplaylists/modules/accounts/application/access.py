import logging
from uuid import UUID

from syncplaylists.modules.accounts.application.ports import (
    AccountCredentialsWriter,
    ConnectedAccountRepository,
    PlatformProfileRegistry,
    TokenCipher,
)
from syncplaylists.modules.accounts.application.use_cases import encrypt_credentials, token_aad
from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.modules.accounts.domain.errors import AccountNotUsableError
from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    AccountNotAvailableError,
    CredentialsRenewal,
    PlatformCredentials,
)
from syncplaylists.shared_kernel.domain.errors import PlatformAuthError, PlatformError
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport

logger = logging.getLogger(__name__)


class AccountAccessService:
    """Реализация shared_kernel.AccountAccessProvider — публичный канал accounts для
    других контекстов. Проверяет владельца/статус/площадку и расшифровывает токены.
    Расшифрованное не покидает процесс: наружу (ARQ, события, логи) — только account_id."""

    def __init__(
        self,
        accounts: ConnectedAccountRepository,
        cipher: TokenCipher,
        writer: AccountCredentialsWriter,
        profiles: PlatformProfileRegistry,
    ) -> None:
        self._accounts = accounts
        self._cipher = cipher
        self._writer = writer
        self._profiles = profiles

    async def get(self, user_id: UUID, account_id: UUID) -> AccountAccess:
        account = await self._accounts.get(account_id)
        if account is None or account.user_id != user_id:
            raise AccountNotAvailableError(f"Аккаунт {account_id} не найден")
        return self._to_access(account)

    async def for_platform(self, user_id: UUID, platform: Platform) -> AccountAccess:
        account = await self._accounts.find_active(user_id, platform)
        if account is None:
            raise AccountNotAvailableError(f"Нет подключённого аккаунта {platform}")
        return self._to_access(account)

    async def update_credentials(self, account_id: UUID, credentials: PlatformCredentials) -> None:
        access, refresh = encrypt_credentials(self._cipher, account_id, credentials)

        def change(account: ConnectedAccount) -> None:
            account.refresh_credentials(access, refresh, credentials.expires_at)

        if not await self._writer.apply(account_id, change):
            raise AccountNotAvailableError(f"Аккаунт {account_id} не найден")

    async def report_auth_failure(self, account_id: UUID) -> bool:
        account = await self._accounts.get(account_id)
        if account is None:
            raise AccountNotAvailableError(f"Аккаунт {account_id} не найден")
        try:
            access = self._to_access(account)
        except AccountNotAvailableError:
            return True  # уже не ACTIVE — для вызывающего это тоже «аккаунт не годен»
        if access.credentials is None:
            # Через расширение «токен протух» не бывает: выход из площадки в браузере —
            # пауза переноса (ExtensionUnavailableError), а не EXPIRED аккаунта.
            return False

        # 401 бывает и разовым (сбой на стороне площадки): прежде чем требовать от
        # пользователя переподключиться, один раз перепроверяем токен через профиль.
        fetcher = self._profiles.get(account.platform)
        if fetcher is not None:
            try:
                await fetcher.fetch(access.credentials)
            except PlatformAuthError:
                pass
            except PlatformError as exc:
                logger.warning(
                    "Перепроверка токена аккаунта %s не удалась (%s), считаем ошибку разовой",
                    account_id,
                    type(exc).__name__,
                )
                return False
            else:
                return False

        def change(target: ConnectedAccount) -> None:
            target.mark_expired()

        await self._writer.apply(account_id, change)
        return True

    def _to_access(self, account: ConnectedAccount) -> AccountAccess:
        if account.transport is Transport.EXTENSION:
            try:
                account.ensure_active()
            except AccountNotUsableError as exc:
                raise AccountNotAvailableError(str(exc)) from exc
            return AccountAccess(
                account_id=account.id,
                user_id=account.user_id,
                platform=account.platform,
                transport=account.transport,
                external_user_id=account.external_user_id,
                credentials=None,
            )
        try:
            access_token = account.ensure_usable()
        except AccountNotUsableError as exc:
            raise AccountNotAvailableError(str(exc)) from exc
        refresh_token = (
            self._cipher.decrypt(
                account.refresh_token.ciphertext, aad=token_aad(account.id, "refresh")
            )
            if account.refresh_token is not None
            else None
        )
        return AccountAccess(
            account_id=account.id,
            user_id=account.user_id,
            platform=account.platform,
            transport=account.transport,
            external_user_id=account.external_user_id,
            credentials=PlatformCredentials(
                access_token=self._cipher.decrypt(
                    access_token.ciphertext, aad=token_aad(account.id, "access")
                ),
                refresh_token=refresh_token,
                expires_at=account.expires_at,
            ),
        )


class AccountCredentialsRefresher:
    """shared_kernel.CredentialsRefresher: OAuth refresh под блокировкой строки аккаунта.
    Блокировку держит AccountCredentialsWriter (своя транзакция, SELECT ... FOR UPDATE),
    поэтому два воркера не обновят токен одновременно — второй дождётся первого и
    получит уже обновлённые токены."""

    def __init__(self, cipher: TokenCipher, writer: AccountCredentialsWriter) -> None:
        self._cipher = cipher
        self._writer = writer

    async def refresh(
        self, account_id: UUID, stale: PlatformCredentials, renew: CredentialsRenewal
    ) -> PlatformCredentials:
        result: list[PlatformCredentials] = []

        async def change(account: ConnectedAccount) -> None:
            current = self._decrypt(account)
            if current.access_token != stale.access_token:
                result.append(current)  # уже обновил другой воркер
                return
            fresh = await renew(current)
            access, refresh = encrypt_credentials(self._cipher, account.id, fresh)
            account.refresh_credentials(access, refresh, fresh.expires_at)
            result.append(fresh)

        if not await self._writer.apply_async(account_id, change):
            raise AccountNotAvailableError(f"Аккаунт {account_id} не найден")
        return result[0]

    def _decrypt(self, account: ConnectedAccount) -> PlatformCredentials:
        try:
            access_token = account.ensure_usable()
        except AccountNotUsableError as exc:
            raise AccountNotAvailableError(str(exc)) from exc
        refresh_token = (
            self._cipher.decrypt(
                account.refresh_token.ciphertext, aad=token_aad(account.id, "refresh")
            )
            if account.refresh_token is not None
            else None
        )
        return PlatformCredentials(
            access_token=self._cipher.decrypt(
                access_token.ciphertext, aad=token_aad(account.id, "access")
            ),
            refresh_token=refresh_token,
            expires_at=account.expires_at,
        )

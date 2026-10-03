from uuid import UUID

from syncplaylists.modules.accounts.application.ports import (
    AccountCredentialsWriter,
    ConnectedAccountRepository,
    TokenCipher,
)
from syncplaylists.modules.accounts.application.use_cases import encrypt_credentials, token_aad
from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.modules.accounts.domain.errors import AccountNotUsableError
from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    AccountNotAvailableError,
    PlatformCredentials,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform


class AccountAccessService:
    """Реализация shared_kernel.AccountAccessProvider — публичный канал accounts для
    других контекстов. Проверяет владельца/статус/площадку и расшифровывает токены.
    Расшифрованное не покидает процесс: наружу (ARQ, события, логи) — только account_id."""

    def __init__(
        self,
        accounts: ConnectedAccountRepository,
        cipher: TokenCipher,
        writer: AccountCredentialsWriter,
    ) -> None:
        self._accounts = accounts
        self._cipher = cipher
        self._writer = writer

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

    def _to_access(self, account: ConnectedAccount) -> AccountAccess:
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
            credentials=PlatformCredentials(
                access_token=self._cipher.decrypt(
                    access_token.ciphertext, aad=token_aad(account.id, "access")
                ),
                refresh_token=refresh_token,
                expires_at=account.expires_at,
            ),
        )

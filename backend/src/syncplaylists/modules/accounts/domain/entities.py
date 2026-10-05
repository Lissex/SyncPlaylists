from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from syncplaylists.modules.accounts.domain.errors import (
    AccountNotUsableError,
    InvalidAccountTransitionError,
)
from syncplaylists.modules.accounts.domain.events import AccountConnected, AccountDisconnected
from syncplaylists.modules.accounts.domain.value_objects import AccountStatus, EncryptedToken
from syncplaylists.shared_kernel.domain.base import AggregateRoot
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport


@dataclass(eq=False, slots=True)
class ConnectedAccount(AggregateRoot):
    user_id: UUID
    platform: Platform
    transport: Transport
    external_user_id: str
    display_name: str | None
    access_token: EncryptedToken | None
    refresh_token: EncryptedToken | None
    expires_at: datetime | None
    status: AccountStatus
    connected_at: datetime

    @classmethod
    def connect(
        cls,
        *,
        account_id: UUID,
        user_id: UUID,
        platform: Platform,
        transport: Transport,
        external_user_id: str,
        display_name: str | None,
        access_token: EncryptedToken | None,
        refresh_token: EncryptedToken | None,
        expires_at: datetime | None,
        now: datetime,
    ) -> "ConnectedAccount":
        if not external_user_id:
            raise ValueError("external_user_id не может быть пустым")
        _ensure_tokens_match_transport(transport, access_token, refresh_token)
        account = cls(
            id=account_id,
            user_id=user_id,
            platform=platform,
            transport=transport,
            external_user_id=external_user_id,
            display_name=display_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            status=AccountStatus.ACTIVE,
            connected_at=now,
        )
        account.record_event(
            AccountConnected(
                occurred_at=now, account_id=account_id, user_id=user_id, platform=platform
            )
        )
        return account

    def reconnect(
        self,
        *,
        transport: Transport,
        display_name: str | None,
        access_token: EncryptedToken | None,
        refresh_token: EncryptedToken | None,
        expires_at: datetime | None,
        now: datetime,
    ) -> None:
        """Повторное подключение того же внешнего аккаунта (из любого статуса), в том
        числе смена транспорта: через расширение токены не хранятся и стираются."""
        _ensure_tokens_match_transport(transport, access_token, refresh_token)
        self.transport = transport
        if display_name is not None:
            self.display_name = display_name
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.status = AccountStatus.ACTIVE
        self.connected_at = now
        self.record_event(
            AccountConnected(
                occurred_at=now, account_id=self.id, user_id=self.user_id, platform=self.platform
            )
        )

    def refresh_credentials(
        self,
        access_token: EncryptedToken,
        refresh_token: EncryptedToken | None,
        expires_at: datetime | None,
    ) -> None:
        """Адаптер обновил OAuth-токены. Отключённый пользователем аккаунт так не
        «оживить» — только явным reconnect."""
        if self.status is AccountStatus.DISCONNECTED:
            raise InvalidAccountTransitionError(f"Аккаунт {self.id} отключён")
        if self.transport is Transport.EXTENSION:
            raise InvalidAccountTransitionError(
                f"Аккаунт {self.id} работает через расширение — токены не хранятся"
            )
        self.access_token = access_token
        # Не все площадки ротируют refresh_token — если новый не выдан, старый в силе.
        if refresh_token is not None:
            self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.status = AccountStatus.ACTIVE

    def mark_expired(self) -> None:
        if self.status is AccountStatus.ACTIVE:
            self.status = AccountStatus.EXPIRED

    def disconnect(self, now: datetime) -> None:
        if self.status is AccountStatus.DISCONNECTED:
            return
        # Строку не удаляем — на неё ссылаются FK из transfers; токены стираем сразу.
        self.access_token = None
        self.refresh_token = None
        self.expires_at = None
        self.status = AccountStatus.DISCONNECTED
        self.record_event(
            AccountDisconnected(
                occurred_at=now, account_id=self.id, user_id=self.user_id, platform=self.platform
            )
        )

    def ensure_active(self) -> None:
        if self.status is not AccountStatus.ACTIVE:
            raise AccountNotUsableError(f"Аккаунт {self.id} в статусе {self.status}")

    def ensure_usable(self) -> EncryptedToken:
        """Токен аккаунта с токенами (не EXTENSION)."""
        self.ensure_active()
        if self.access_token is None:
            raise AccountNotUsableError(f"У аккаунта {self.id} нет токена")
        return self.access_token


def _ensure_tokens_match_transport(
    transport: Transport, access_token: EncryptedToken | None, refresh_token: EncryptedToken | None
) -> None:
    """Через расширение запросы идут из браузера пользователя — токенов площадки у нас
    нет и быть не должно. У остальных транспортов без токена делать нечего."""
    if transport is Transport.EXTENSION:
        if access_token is not None or refresh_token is not None:
            raise ValueError("Аккаунт через расширение не хранит токены площадки")
    elif access_token is None:
        raise ValueError(f"Для транспорта {transport} нужен токен")

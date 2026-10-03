from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr

from syncplaylists.modules.accounts.application.dto import AccountDto
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport


class ConnectAccountRequest(BaseModel):
    """Ручное подключение по токену (VK/Яндекс и т.п., где нет публичного OAuth).
    Пароль площадки не принимаем никогда — только уже выданный токен."""

    platform: Platform
    transport: Transport = Transport.UNOFFICIAL
    external_user_id: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = None
    expires_at: datetime | None = None


class AccountResponse(BaseModel):
    # Токенов (даже зашифрованных) в ответе нет и быть не должно.
    id: UUID
    platform: Platform
    transport: Transport
    status: str
    external_user_id: str
    display_name: str | None
    expires_at: datetime | None
    connected_at: datetime

    @classmethod
    def from_dto(cls, dto: AccountDto) -> "AccountResponse":
        return cls(
            id=dto.id,
            platform=dto.platform,
            transport=dto.transport,
            status=dto.status,
            external_user_id=dto.external_user_id,
            display_name=dto.display_name,
            expires_at=dto.expires_at,
            connected_at=dto.connected_at,
        )

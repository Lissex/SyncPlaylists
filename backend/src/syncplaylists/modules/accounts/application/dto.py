from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport


@dataclass(frozen=True, slots=True)
class AccountDto:
    """Наружу (API) — без токенов, даже зашифрованных."""

    id: UUID
    platform: Platform
    transport: Transport
    status: str
    external_user_id: str
    display_name: str | None
    expires_at: datetime | None
    connected_at: datetime

    @classmethod
    def from_domain(cls, account: ConnectedAccount) -> "AccountDto":
        return cls(
            id=account.id,
            platform=account.platform,
            transport=account.transport,
            status=account.status.value,
            external_user_id=account.external_user_id,
            display_name=account.display_name,
            expires_at=account.expires_at,
            connected_at=account.connected_at,
        )

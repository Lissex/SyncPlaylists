from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from syncplaylists.modules.extension.domain.entities import ExtensionDevice


@dataclass(frozen=True, slots=True)
class DeviceDto:
    id: UUID
    user_id: UUID
    name: str
    browser: str
    version: str
    created_at: datetime
    last_seen_at: datetime | None
    revoked: bool

    @classmethod
    def from_domain(cls, device: ExtensionDevice) -> "DeviceDto":
        return cls(
            id=device.id,
            user_id=device.user_id,
            name=device.name,
            browser=device.browser,
            version=device.version,
            created_at=device.created_at,
            last_seen_at=device.last_seen_at,
            revoked=device.revoked_at is not None,
        )


@dataclass(frozen=True, slots=True)
class PairingStarted:
    pairing_id: str = field(repr=False)  # секрет расширения: по нему оно заберёт токен
    user_code: str  # показывается человеку, вводится на сайте
    expires_in: int
    interval: int


@dataclass(frozen=True, slots=True)
class PairingResult:
    """Токен устройства отдаётся расширению ОДИН раз; у нас хранится только sha256."""

    device_id: UUID
    device_token: str = field(repr=False)

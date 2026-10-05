from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from syncplaylists.modules.extension.domain.events import (
    ExtensionDevicePaired,
    ExtensionDeviceRevoked,
)
from syncplaylists.shared_kernel.domain.base import AggregateRoot


@dataclass(eq=False, slots=True)
class ExtensionDevice(AggregateRoot):
    """Браузерное расширение, привязанное к пользователю одноразовым кодом. Хранится
    только sha256 токена устройства: сам токен знает лишь расширение. Отозванное или
    просроченное устройство больше не получает задач."""

    user_id: UUID
    name: str
    browser: str
    version: str
    token_hash: str
    created_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None

    @classmethod
    def pair(
        cls,
        *,
        device_id: UUID,
        user_id: UUID,
        name: str,
        browser: str,
        version: str,
        token_hash: str,
        now: datetime,
    ) -> "ExtensionDevice":
        if not token_hash:
            raise ValueError("token_hash не может быть пустым")
        device = cls(
            id=device_id,
            user_id=user_id,
            name=name,
            browser=browser,
            version=version,
            token_hash=token_hash,
            created_at=now,
        )
        device.record_event(
            ExtensionDevicePaired(occurred_at=now, device_id=device_id, user_id=user_id)
        )
        return device

    def is_usable(self, now: datetime, token_ttl: timedelta) -> bool:
        return self.revoked_at is None and now < self.created_at + token_ttl

    def touch(self, now: datetime, version: str | None = None) -> None:
        self.last_seen_at = now
        if version:
            self.version = version

    def revoke(self, now: datetime) -> None:
        if self.revoked_at is not None:
            return
        self.revoked_at = now
        self.record_event(
            ExtensionDeviceRevoked(occurred_at=now, device_id=self.id, user_id=self.user_id)
        )

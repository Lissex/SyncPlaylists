from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from syncplaylists.infrastructure.db.base import Base
from syncplaylists.modules.accounts.domain.value_objects import AccountStatus
from syncplaylists.shared_kernel.domain.value_objects import Transport

# Значения CHECK-constraint'ов генерируются из StrEnum (как в transfers/orm.py).
_STATUS_SQL = ", ".join(f"'{s.value}'" for s in AccountStatus)
_TRANSPORT_SQL = ", ".join(f"'{t.value}'" for t in Transport)


class ConnectedAccountOrm(Base):
    __tablename__ = "connected_accounts"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "platform", "external_user_id", name="uq_connected_accounts_external"
        ),
        # Один активный аккаунт на (user, platform) — ARCHITECTURE.md, 11b.
        Index(
            "uq_connected_accounts_one_active",
            "user_id",
            "platform",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_connected_accounts_status"),
        CheckConstraint(f"transport IN ({_TRANSPORT_SQL})", name="ck_connected_accounts_transport"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    transport: Mapped[str] = mapped_column(String(20), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # AES-GCM (TokenCipher): версия | nonce | ciphertext+tag. NULL — после отключения.
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

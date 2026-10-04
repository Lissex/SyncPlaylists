from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from syncplaylists.infrastructure.db.base import Base
from syncplaylists.modules.transfers.domain.value_objects import TransferItemStatus, TransferStatus

# Значения CHECK-constraint'ов генерируются из Python StrEnum, а не дублируются
# строками в SQL, чтобы не расходиться с доменом при добавлении нового статуса.
_TRANSFER_STATUS_VALUES = tuple(status.value for status in TransferStatus)
_TRANSFER_ITEM_STATUS_VALUES = tuple(status.value for status in TransferItemStatus)
_TRANSFER_STATUS_SQL = ", ".join(f"'{v}'" for v in _TRANSFER_STATUS_VALUES)
_TRANSFER_ITEM_STATUS_SQL = ", ".join(f"'{v}'" for v in _TRANSFER_ITEM_STATUS_VALUES)


class TransferOrm(Base):
    __tablename__ = "transfers"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_playlist_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # RESTRICT: аккаунты физически не удаляются, только переводятся в DISCONNECTED —
    # история переносов продолжает на них ссылаться.
    source_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connected_accounts.id", ondelete="RESTRICT"), nullable=True
    )
    source_file_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_file_format: Mapped[str | None] = mapped_column(String(10), nullable=True)

    destination_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target_platform: Mapped[str] = mapped_column(String(20), nullable=False)
    target_playlist_id: Mapped[str | None] = mapped_column(String, nullable=True)
    new_playlist_title: Mapped[str | None] = mapped_column(String, nullable=True)
    new_playlist_description: Mapped[str | None] = mapped_column(String, nullable=True)
    destination_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connected_accounts.id", ondelete="RESTRICT"), nullable=True
    )

    resolved_target_platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # external_id созданных плейлистов по порядку частей (1/N, 2/N, ...), площадка —
    # resolved_target_platform.
    resolved_target_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    cursor: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # PAUSED_QUOTA: когда продолжить и в какую фазу вернуться (queued|running|writing).
    resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_from: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Счётчики items (TransferProgress). run_match меняет их атомарно
    # (`SET matched = matched + 1`), полное сохранение агрегата — пересчитывает из items.
    total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    pending: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    matched: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    uncertain: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    not_found: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    added: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["TransferItemOrm"]] = relationship(
        back_populates="transfer",
        cascade="all, delete-orphan",
        order_by="TransferItemOrm.position",
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_TRANSFER_STATUS_SQL})", name="ck_transfers_status"),
    )


class TransferItemOrm(Base):
    __tablename__ = "transfer_items"
    __table_args__ = (
        UniqueConstraint("transfer_id", "position"),
        CheckConstraint(
            f"status IN ({_TRANSFER_ITEM_STATUS_SQL})", name="ck_transfer_items_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    transfer_id: Mapped[UUID] = mapped_column(
        ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_pt_id: Mapped[UUID] = mapped_column(ForeignKey("platform_tracks.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # Денормализованный MatchResult, не FK на track_matches: при ручном разрешении
    # (resolve_item, method="manual") строки в track_matches не существует — а
    # придумывать её специально ради FK было бы искусственно. См. долг в
    # ARCHITECTURE.md: связь с track_matches.id можно восстановить на этапе
    # library_tools, когда появится осмысленный сценарий её использования.
    match_target_platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    match_target_external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    match_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_restriction: Mapped[str | None] = mapped_column(String(20), nullable=True)

    candidates: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    # Когда item вышел из PENDING (результат run_match) — по последним таким отметкам
    # оценивается оставшееся время переноса. Ставит БД в save_item_outcome.
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transfer: Mapped[TransferOrm] = relationship(back_populates="items")

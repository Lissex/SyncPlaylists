from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from syncplaylists.infrastructure.db.base import Base


class RecognitionOrm(Base):
    """Кэш распознаваний: одна строка на трек источника (ARCHITECTURE.md, 10 и 11j).
    Звук не хранится — только результат."""

    __tablename__ = "recognitions"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_pt_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_tracks.id"), unique=True, nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    no_audio_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    artist: Mapped[str | None] = mapped_column(String, nullable=True)
    isrc: Mapped[str | None] = mapped_column(String(12), nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    exact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    offset_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_skew: Mapped[float | None] = mapped_column(Float, nullable=True)
    frequency_skew: Mapped[float | None] = mapped_column(Float, nullable=True)
    window_start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_length_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

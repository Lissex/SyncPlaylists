from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from syncplaylists.infrastructure.db.base import Base


class CanonicalTrackOrm(Base):
    __tablename__ = "canonical_tracks"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    isrc: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True)
    title_norm: Mapped[str] = mapped_column(String, nullable=False)
    artist_norm: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mbid: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PlatformTrackOrm(Base):
    __tablename__ = "platform_tracks"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    canonical_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_tracks.id"), nullable=True
    )
    raw_title: Mapped[str] = mapped_column(String, nullable=False)
    raw_artist: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    isrc: Mapped[str | None] = mapped_column(String(12), nullable=True)

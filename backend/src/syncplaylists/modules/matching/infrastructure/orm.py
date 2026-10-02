from uuid import UUID

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from syncplaylists.infrastructure.db.base import Base


class TrackMatchOrm(Base):
    __tablename__ = "track_matches"
    __table_args__ = (UniqueConstraint("source_pt_id", "target_platform"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_pt_id: Mapped[UUID] = mapped_column(ForeignKey("platform_tracks.id"), nullable=False)
    target_platform: Mapped[str] = mapped_column(String(20), nullable=False)
    target_pt_id: Mapped[UUID] = mapped_column(ForeignKey("platform_tracks.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    confirmations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

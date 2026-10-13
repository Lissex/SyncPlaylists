"""Этап 6-1: кэш распознаваний по звуку.

recognitions — одна строка на трек источника (UNIQUE source_pt_id): исход
(recognized | no_match | no_audio), что распознано, сдвиги лучшего совпадения
(time_skew, frequency_skew — защита от ложных совпадений на изменённой скорости, 11j),
окно фрагмента и срок жизни отрицательных исходов. Звук не хранится.

Revision ID: d4f1a8c3e6b2
Revises: 1b6e9f3c7a20
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f1a8c3e6b2"
down_revision: str | Sequence[str] | None = "1b6e9f3c7a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recognitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_pt_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("no_audio_reason", sa.String(length=20), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("artist", sa.String(), nullable=True),
        sa.Column("isrc", sa.String(length=12), nullable=True),
        sa.Column("provider_ref", sa.String(), nullable=True),
        sa.Column("exact", sa.Boolean(), nullable=False),
        sa.Column("offset_ms", sa.Integer(), nullable=True),
        sa.Column("time_skew", sa.Float(), nullable=True),
        sa.Column("frequency_skew", sa.Float(), nullable=True),
        sa.Column("window_start_ms", sa.Integer(), nullable=True),
        sa.Column("window_length_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_pt_id"], ["platform_tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_pt_id"),
    )


def downgrade() -> None:
    op.drop_table("recognitions")

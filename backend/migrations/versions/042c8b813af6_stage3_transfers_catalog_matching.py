"""Этап 3: таблицы canonical_tracks, platform_tracks, track_matches, transfers,
transfer_items + расширения pg_trgm и unaccent.

Revision ID: 042c8b813af6
Revises:
Create Date: 2026-10-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "042c8b813af6"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Значения CHECK-constraint'ов — литеральная копия TransferStatus/TransferItemStatus
# (syncplaylists.modules.transfers.domain.value_objects) на момент написания этой
# миграции. Миграции — застывший исторический артефакт: последующие изменения enum
# оформляются НОВОЙ миграцией (ALTER TABLE ... DROP/ADD CONSTRAINT), а не правкой этого
# файла задним числом.
_TRANSFER_STATUSES = ("queued", "running", "paused_captcha", "review", "writing", "done", "failed")
_TRANSFER_ITEM_STATUSES = ("pending", "matched", "uncertain", "not_found", "added", "failed")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    op.create_table(
        "canonical_tracks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("isrc", sa.String(length=12), nullable=True),
        sa.Column("title_norm", sa.String(), nullable=False),
        sa.Column("artist_norm", sa.String(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("mbid", sa.String(length=36), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("isrc"),
    )
    op.create_index(
        "ix_canonical_tracks_title_norm_trgm",
        "canonical_tracks",
        ["title_norm"],
        postgresql_using="gin",
        postgresql_ops={"title_norm": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_canonical_tracks_artist_norm_trgm",
        "canonical_tracks",
        ["artist_norm"],
        postgresql_using="gin",
        postgresql_ops={"artist_norm": "gin_trgm_ops"},
    )

    op.create_table(
        "platform_tracks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("canonical_id", sa.Uuid(), nullable=True),
        sa.Column("raw_title", sa.String(), nullable=False),
        sa.Column("raw_artist", sa.String(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("isrc", sa.String(length=12), nullable=True),
        sa.ForeignKeyConstraint(["canonical_id"], ["canonical_tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "external_id"),
    )

    op.create_table(
        "track_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_pt_id", sa.Uuid(), nullable=False),
        sa.Column("target_platform", sa.String(length=20), nullable=False),
        sa.Column("target_pt_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confirmations", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["source_pt_id"], ["platform_tracks.id"]),
        sa.ForeignKeyConstraint(["target_pt_id"], ["platform_tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_pt_id", "target_platform"),
    )

    op.create_table(
        "transfers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("source_platform", sa.String(length=20), nullable=True),
        sa.Column("source_playlist_id", sa.String(), nullable=True),
        sa.Column("source_account_id", sa.Uuid(), nullable=True),
        sa.Column("source_file_id", sa.Uuid(), nullable=True),
        sa.Column("source_file_format", sa.String(length=10), nullable=True),
        sa.Column("destination_kind", sa.String(length=20), nullable=False),
        sa.Column("target_platform", sa.String(length=20), nullable=False),
        sa.Column("target_playlist_id", sa.String(), nullable=True),
        sa.Column("new_playlist_title", sa.String(), nullable=True),
        sa.Column("new_playlist_description", sa.String(), nullable=True),
        sa.Column("destination_account_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_target_platform", sa.String(length=20), nullable=True),
        sa.Column("resolved_target_playlist_id", sa.String(), nullable=True),
        sa.Column("cursor", JSONB(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in _TRANSFER_STATUSES) + ")",
            name="ck_transfers_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transfers_user_id", "transfers", ["user_id"])

    op.create_table(
        "transfer_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transfer_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_pt_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("match_target_platform", sa.String(length=20), nullable=True),
        sa.Column("match_target_external_id", sa.String(), nullable=True),
        sa.Column("match_method", sa.String(length=20), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("candidates", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["transfer_id"], ["transfers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_pt_id"], ["platform_tracks.id"]),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in _TRANSFER_ITEM_STATUSES) + ")",
            name="ck_transfer_items_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transfer_id", "position"),
    )


def downgrade() -> None:
    op.drop_table("transfer_items")
    op.drop_index("ix_transfers_user_id", table_name="transfers")
    op.drop_table("transfers")
    op.drop_table("track_matches")
    op.drop_table("platform_tracks")
    op.drop_index("ix_canonical_tracks_artist_norm_trgm", table_name="canonical_tracks")
    op.drop_index("ix_canonical_tracks_title_norm_trgm", table_name="canonical_tracks")
    op.drop_table("canonical_tracks")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")

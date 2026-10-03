"""Этап 4b-2 (SoundCloud): несколько созданных плейлистов на перенос и пометка
ограничения найденного трека.

- transfers.resolved_target_ids (jsonb-массив external_id) вместо одной колонки
  resolved_target_playlist_id: новый плейлист SoundCloud вмещает 500 треков, больший
  перенос раскладывается на «<название> (1/N)», «(2/N)», ... Существующее значение
  переносится первым элементом. downgrade оставляет только первый плейлист.
- track_matches.restriction и transfer_items.match_restriction — например,
  "preview_only" (SoundCloud Go+): пометка для отчёта, переживает попадание в кэш.

Revision ID: e7b2c9d4a1f6
Revises: d4f1a7c2e8b3
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e7b2c9d4a1f6"
down_revision: str | Sequence[str] | None = "d4f1a7c2e8b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transfers",
        sa.Column(
            "resolved_target_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE transfers SET resolved_target_ids = jsonb_build_array(resolved_target_playlist_id)"
        " WHERE resolved_target_playlist_id IS NOT NULL"
    )
    op.drop_column("transfers", "resolved_target_playlist_id")
    op.add_column("track_matches", sa.Column("restriction", sa.String(20), nullable=True))
    op.add_column("transfer_items", sa.Column("match_restriction", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("transfer_items", "match_restriction")
    op.drop_column("track_matches", "restriction")
    op.add_column("transfers", sa.Column("resolved_target_playlist_id", sa.String(), nullable=True))
    op.execute(
        "UPDATE transfers SET resolved_target_playlist_id = resolved_target_ids ->> 0"
        " WHERE jsonb_array_length(resolved_target_ids) > 0"
    )
    op.drop_column("transfers", "resolved_target_ids")

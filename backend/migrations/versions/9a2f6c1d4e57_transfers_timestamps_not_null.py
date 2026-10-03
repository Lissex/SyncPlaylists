"""Дрейф этапа 3: transfers.created_at/updated_at — NOT NULL, как в ORM.

Миграция 042c8b813af6 создала колонки nullable (server_default есть, но NOT NULL не
выставлен), а ORM объявляет их NOT NULL. Старую миграцию не правим — выравниваем здесь:
сначала backfill возможных NULL, потом SET NOT NULL.

(Второй найденный дрейф — GIN trgm-индексы canonical_tracks — исправлен в ORM, схема
БД там правильная, миграция не нужна.)

Revision ID: 9a2f6c1d4e57
Revises: 7c1e4a9b2d30
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a2f6c1d4e57"
down_revision: str | Sequence[str] | None = "7c1e4a9b2d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE transfers SET created_at = now() WHERE created_at IS NULL")
    op.execute(
        "UPDATE transfers SET updated_at = COALESCE(created_at, now()) WHERE updated_at IS NULL"
    )
    op.alter_column(
        "transfers", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.alter_column(
        "transfers", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )


def downgrade() -> None:
    op.alter_column(
        "transfers", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )
    op.alter_column(
        "transfers", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )

"""transfer_items.processed_at — когда item вышел из PENDING.

По последним таким отметкам GET /transfers/{id} и SSE оценивают оставшееся время
переноса (скорость последних N треков). Для уже существующих items значение не
восстанавливается (NULL): оценка появится по мере обработки новых.

Revision ID: d4f1a7c2e8b3
Revises: b8d3e0f7a912
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f1a7c2e8b3"
down_revision: str | Sequence[str] | None = "b8d3e0f7a912"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transfer_items",
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transfer_items", "processed_at")

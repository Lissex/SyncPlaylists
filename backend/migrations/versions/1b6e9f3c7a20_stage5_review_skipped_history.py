"""Этап 5-0: пропуск на ревью, название источника, история переносов.

- статус item `skipped` (пропущен пользователем) в CHECK-constraint и счётчик
  transfers.skipped — раньше «Пропустить» писало `failed`, и в отчёте пропуск
  не отличался от сбоя;
- transfers.source_title — название источника для истории и отчёта;
- индекс (user_id, created_at) — история переносов новыми первыми.

Revision ID: 1b6e9f3c7a20
Revises: c9e4b2a7d815
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1b6e9f3c7a20"
down_revision: str | Sequence[str] | None = "c9e4b2a7d815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ITEM_STATUSES_BEFORE = ("pending", "matched", "uncertain", "not_found", "added", "failed")
_ITEM_STATUSES_AFTER = (*_ITEM_STATUSES_BEFORE, "skipped")


def _check(statuses: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{s}'" for s in statuses) + ")"


def upgrade() -> None:
    op.add_column(
        "transfers",
        sa.Column("skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("transfers", sa.Column("source_title", sa.String(), nullable=True))
    op.drop_constraint("ck_transfer_items_status", "transfer_items", type_="check")
    op.create_check_constraint(
        "ck_transfer_items_status", "transfer_items", _check(_ITEM_STATUSES_AFTER)
    )
    op.create_index("ix_transfers_user_id_created_at", "transfers", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_transfers_user_id_created_at", table_name="transfers")
    # Старая схема знала пропуск только как failed.
    op.execute("UPDATE transfer_items SET status = 'failed' WHERE status = 'skipped'")
    op.execute("UPDATE transfers SET failed = failed + skipped")
    op.drop_constraint("ck_transfer_items_status", "transfer_items", type_="check")
    op.create_check_constraint(
        "ck_transfer_items_status", "transfer_items", _check(_ITEM_STATUSES_BEFORE)
    )
    op.drop_column("transfers", "source_title")
    op.drop_column("transfers", "skipped")

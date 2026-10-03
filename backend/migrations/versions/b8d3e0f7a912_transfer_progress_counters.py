"""Счётчики прогресса в transfers (total/pending/matched/uncertain/not_found/added/failed).

run_match больше не сохраняет агрегат Transfer целиком: он условно обновляет один
transfer_items и атомарно сдвигает эти счётчики (`SET matched = matched + 1`), а переход
RUNNING → REVIEW/WRITING делает джоба, получившая pending = 0. Для существующих
переносов счётчики заполняются из transfer_items.

Revision ID: b8d3e0f7a912
Revises: 9a2f6c1d4e57
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d3e0f7a912"
down_revision: str | Sequence[str] | None = "9a2f6c1d4e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUNTERS = ("total", "pending", "matched", "uncertain", "not_found", "added", "failed")


def upgrade() -> None:
    for column in _COUNTERS:
        op.add_column(
            "transfers",
            sa.Column(column, sa.Integer(), server_default=sa.text("0"), nullable=False),
        )
    op.execute(
        """
        UPDATE transfers AS t SET
            total = c.total,
            pending = c.pending,
            matched = c.matched,
            uncertain = c.uncertain,
            not_found = c.not_found,
            added = c.added,
            failed = c.failed
        FROM (
            SELECT
                transfer_id,
                count(*) AS total,
                count(*) FILTER (WHERE status = 'pending') AS pending,
                count(*) FILTER (WHERE status = 'matched') AS matched,
                count(*) FILTER (WHERE status = 'uncertain') AS uncertain,
                count(*) FILTER (WHERE status = 'not_found') AS not_found,
                count(*) FILTER (WHERE status = 'added') AS added,
                count(*) FILTER (WHERE status = 'failed') AS failed
            FROM transfer_items
            GROUP BY transfer_id
        ) AS c
        WHERE c.transfer_id = t.id
        """
    )


def downgrade() -> None:
    for column in reversed(_COUNTERS):
        op.drop_column("transfers", column)

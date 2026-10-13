"""Этап 6-2: распознавание по звуку в переносах.

- transfers.recognize_audio — «Распознавать по звуку» (по умолчанию да);
- transfers.recognition_deadline_at — потолок фазы распознавания переноса;
- transfer_items.recognition_state (queued | ready | final) — для условных UPDATE;
- transfer_items.recognition (jsonb) — текстовый итог, причина, снимок услышанного.

Revision ID: e8a2c5f1b7d4
Revises: d4f1a8c3e6b2
Create Date: 2026-10-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e8a2c5f1b7d4"
down_revision: str | Sequence[str] | None = "d4f1a8c3e6b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transfers",
        sa.Column("recognize_audio", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "transfers",
        sa.Column("recognition_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transfer_items", sa.Column("recognition_state", sa.String(length=10), nullable=True)
    )
    op.add_column(
        "transfer_items",
        sa.Column("recognition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transfer_items", "recognition")
    op.drop_column("transfer_items", "recognition_state")
    op.drop_column("transfers", "recognition_deadline_at")
    op.drop_column("transfers", "recognize_audio")

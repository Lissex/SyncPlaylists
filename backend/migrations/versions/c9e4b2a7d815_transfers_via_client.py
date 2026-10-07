"""Этап 4c-2: transfers.via_client — перенос через браузерное расширение (задачи — в
отдельной очереди ARQ, запись — пачками).

Revision ID: c9e4b2a7d815
Revises: a5c7e2f9b134
Create Date: 2026-10-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e4b2a7d815"
down_revision: str | Sequence[str] | None = "a5c7e2f9b134"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transfers",
        sa.Column("via_client", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("transfers", "via_client")

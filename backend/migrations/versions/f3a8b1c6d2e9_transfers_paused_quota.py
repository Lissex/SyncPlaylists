"""transfers: пауза по квоте площадки (PAUSED_QUOTA).

- resume_at — когда перенос продолжится сам;
- paused_from — фаза, в которую вернуться (queued | running | writing);
- статус paused_quota в CHECK-constraint.

Revision ID: f3a8b1c6d2e9
Revises: e7b2c9d4a1f6
Create Date: 2026-10-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a8b1c6d2e9"
down_revision: str | Sequence[str] | None = "e7b2c9d4a1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES_BEFORE = ("queued", "running", "paused_captcha", "review", "writing", "done", "failed")
_STATUSES_AFTER = (
    "queued",
    "running",
    "paused_captcha",
    "paused_quota",
    "review",
    "writing",
    "done",
    "failed",
)


def _check(statuses: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{s}'" for s in statuses) + ")"


def upgrade() -> None:
    op.add_column("transfers", sa.Column("resume_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("transfers", sa.Column("paused_from", sa.String(20), nullable=True))
    op.drop_constraint("ck_transfers_status", "transfers", type_="check")
    op.create_check_constraint("ck_transfers_status", "transfers", _check(_STATUSES_AFTER))


def downgrade() -> None:
    # Переносы на паузе возвращаем в фазу, из которой они ушли, — иначе старый CHECK
    # не примет строку. Продолжит их sweeper.
    op.execute(
        "UPDATE transfers SET status = paused_from WHERE status = 'paused_quota'"
        " AND paused_from IS NOT NULL"
    )
    op.drop_constraint("ck_transfers_status", "transfers", type_="check")
    op.create_check_constraint("ck_transfers_status", "transfers", _check(_STATUSES_BEFORE))
    op.drop_column("transfers", "paused_from")
    op.drop_column("transfers", "resume_at")

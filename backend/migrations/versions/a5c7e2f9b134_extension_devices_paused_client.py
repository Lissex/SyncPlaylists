"""Этап 4c-1: браузерное расширение.

- extension_devices — привязанные к пользователю расширения (хранится только sha256
  токена устройства);
- transfers.pause_reason и статус paused_client в CHECK — перенос ждёт расширение.

Revision ID: a5c7e2f9b134
Revises: f3a8b1c6d2e9
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5c7e2f9b134"
down_revision: str | Sequence[str] | None = "f3a8b1c6d2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES_BEFORE = (
    "queued",
    "running",
    "paused_captcha",
    "paused_quota",
    "review",
    "writing",
    "done",
    "failed",
)
_STATUSES_AFTER = (
    "queued",
    "running",
    "paused_captcha",
    "paused_quota",
    "paused_client",
    "review",
    "writing",
    "done",
    "failed",
)


def _check(statuses: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{s}'" for s in statuses) + ")"


def upgrade() -> None:
    op.create_table(
        "extension_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("browser", sa.String(40), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_extension_devices_token_hash"),
    )
    op.create_index("ix_extension_devices_user_id", "extension_devices", ["user_id"])

    op.add_column("transfers", sa.Column("pause_reason", sa.String(40), nullable=True))
    op.drop_constraint("ck_transfers_status", "transfers", type_="check")
    op.create_check_constraint("ck_transfers_status", "transfers", _check(_STATUSES_AFTER))


def downgrade() -> None:
    # Ждущие расширение переносы возвращаем в фазу, из которой они ушли, — иначе старый
    # CHECK не примет строку. Продолжит их sweeper (QUEUED/RUNNING).
    op.execute(
        "UPDATE transfers SET status = paused_from WHERE status = 'paused_client'"
        " AND paused_from IS NOT NULL"
    )
    op.drop_constraint("ck_transfers_status", "transfers", type_="check")
    op.create_check_constraint("ck_transfers_status", "transfers", _check(_STATUSES_BEFORE))
    op.drop_column("transfers", "pause_reason")

    op.drop_index("ix_extension_devices_user_id", table_name="extension_devices")
    op.drop_table("extension_devices")

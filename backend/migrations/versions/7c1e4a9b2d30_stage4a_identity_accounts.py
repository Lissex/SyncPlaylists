"""Этап 4a: users, connected_accounts + FK transfers → users/connected_accounts.

Revision ID: 7c1e4a9b2d30
Revises: 042c8b813af6
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c1e4a9b2d30"
down_revision: str | Sequence[str] | None = "042c8b813af6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Литеральная копия AccountStatus/Transport на момент написания миграции (как и в
# 042c8b813af6): изменение enum — новой миграцией, а не правкой этого файла.
_ACCOUNT_STATUSES = ("active", "expired", "disconnected")
_TRANSPORTS = ("official", "unofficial", "extension")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "connected_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("transport", sa.String(length=20), nullable=False),
        sa.Column("external_user_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({_in(_ACCOUNT_STATUSES)})", name="ck_connected_accounts_status"
        ),
        sa.CheckConstraint(
            f"transport IN ({_in(_TRANSPORTS)})", name="ck_connected_accounts_transport"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "platform", "external_user_id", name="uq_connected_accounts_external"
        ),
    )
    op.create_index(
        op.f("ix_connected_accounts_user_id"), "connected_accounts", ["user_id"], unique=False
    )
    op.create_index(
        "uq_connected_accounts_one_active",
        "connected_accounts",
        ["user_id", "platform"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # До этапа 4a user_id/account_id в transfers — произвольные UUID без FK (dev-демо
    # этапа 3). users только что создана и пуста, так что все такие переносы — сироты;
    # без их удаления FK не создать. transfer_items уходят каскадом. Прод-данных нет.
    op.execute("DELETE FROM transfers WHERE user_id NOT IN (SELECT id FROM users)")

    op.create_foreign_key(
        "fk_transfers_user_id_users",
        "transfers",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_transfers_source_account_id_connected_accounts",
        "transfers",
        "connected_accounts",
        ["source_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_transfers_destination_account_id_connected_accounts",
        "transfers",
        "connected_accounts",
        ["destination_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Удалённые в upgrade() переносы-сироты не восстанавливаются.
    op.drop_constraint(
        "fk_transfers_destination_account_id_connected_accounts", "transfers", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_transfers_source_account_id_connected_accounts", "transfers", type_="foreignkey"
    )
    op.drop_constraint("fk_transfers_user_id_users", "transfers", type_="foreignkey")
    op.drop_index("uq_connected_accounts_one_active", table_name="connected_accounts")
    op.drop_index(op.f("ix_connected_accounts_user_id"), table_name="connected_accounts")
    op.drop_table("connected_accounts")
    op.drop_table("users")

"""add admin fields and indexes

Revision ID: 20260502_000017
Revises: 20260502_000016
Create Date: 2026-05-02 00:00:17

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260502_000017"
down_revision = "20260502_000016"
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "users", "is_admin"):
        op.add_column(
            "users",
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "users", "ix_users_created_at"):
        op.create_index("ix_users_created_at", "users", ["created_at"])
    if not _has_index(inspector, "users", "ix_users_last_login_at"):
        op.create_index("ix_users_last_login_at", "users", ["last_login_at"])
    if not _has_index(inspector, "users", "ix_users_onboarding_completed"):
        op.create_index("ix_users_onboarding_completed", "users", ["onboarding_completed"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_index(inspector, "users", "ix_users_onboarding_completed"):
        op.drop_index("ix_users_onboarding_completed", table_name="users")
    if _has_index(inspector, "users", "ix_users_last_login_at"):
        op.drop_index("ix_users_last_login_at", table_name="users")
    if _has_index(inspector, "users", "ix_users_created_at"):
        op.drop_index("ix_users_created_at", table_name="users")

    inspector = sa.inspect(bind)
    if _has_column(inspector, "users", "is_admin"):
        op.drop_column("users", "is_admin")

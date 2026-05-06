"""repair users.email_verified

Revision ID: 20260505_000018
Revises: 20260502_000017
Create Date: 2026-05-05 00:00:18

"""

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260505_000018"
down_revision = "20260502_000017"
branch_labels = None
depends_on = None


def _users_has_column(column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = inspector.get_columns("users")
    return any(str(column.get("name")) == column_name for column in columns)


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column(
            "users",
            sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
        return

    if _users_has_column("email_verified"):
        return

    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_column("users", "email_verified")
        return

    if not _users_has_column("email_verified"):
        return

    op.drop_column("users", "email_verified")

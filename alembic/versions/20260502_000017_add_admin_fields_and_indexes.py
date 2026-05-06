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


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_users_created_at", "users", ["created_at"])
    op.create_index("ix_users_last_login_at", "users", ["last_login_at"])
    op.create_index("ix_users_onboarding_completed", "users", ["onboarding_completed"])


def downgrade() -> None:
    op.drop_index("ix_users_onboarding_completed", table_name="users")
    op.drop_index("ix_users_last_login_at", table_name="users")
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_column("users", "is_admin")

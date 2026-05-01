"""extend meta pages authorized storage

Revision ID: 20260426_000011
Revises: 20260417_000010
Create Date: 2026-04-26 00:00:11

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260426_000011"
down_revision = "20260417_000010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meta_pages", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("meta_pages", sa.Column("page_access_token", sa.Text(), nullable=True))
    op.add_column("meta_pages", sa.Column("tasks", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("meta_pages", sa.Column("perms", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column(
        "meta_pages",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_foreign_key("fk_meta_pages_user_id_users", "meta_pages", "users", ["user_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_meta_pages_user_id_users", "meta_pages", type_="foreignkey")
    op.drop_column("meta_pages", "updated_at")
    op.drop_column("meta_pages", "perms")
    op.drop_column("meta_pages", "tasks")
    op.drop_column("meta_pages", "page_access_token")
    op.drop_column("meta_pages", "user_id")

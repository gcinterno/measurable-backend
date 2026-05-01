"""add meta pages cache

Revision ID: 20260330_000007
Revises: 20260319_000006
Create Date: 2026-03-30 00:00:07

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260330_000007"
down_revision = "20260319_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meta_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("business_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("integration_id", "page_id", name="uq_meta_pages_integration_page"),
    )
    op.create_index("ix_meta_pages_integration_id", "meta_pages", ["integration_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_meta_pages_integration_id", table_name="meta_pages")
    op.drop_table("meta_pages")

"""split meta pages and instagram accounts

Revision ID: 20260427_000012
Revises: 20260426_000011
Create Date: 2026-04-27 00:00:12

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260427_000012"
down_revision = "20260426_000011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meta_pages",
        sa.Column("record_type", sa.String(length=50), server_default="facebook_page", nullable=False),
    )
    op.add_column("meta_pages", sa.Column("parent_page_id", sa.String(length=255), nullable=True))
    op.add_column("meta_pages", sa.Column("instagram_username", sa.String(length=255), nullable=True))
    op.add_column("meta_pages", sa.Column("profile_picture_url", sa.String(length=2048), nullable=True))
    op.drop_constraint("uq_meta_pages_integration_page", "meta_pages", type_="unique")
    op.create_unique_constraint(
        "uq_meta_pages_integration_record",
        "meta_pages",
        ["integration_id", "record_type", "page_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_meta_pages_integration_record", "meta_pages", type_="unique")
    op.create_unique_constraint(
        "uq_meta_pages_integration_page",
        "meta_pages",
        ["integration_id", "page_id"],
    )
    op.drop_column("meta_pages", "profile_picture_url")
    op.drop_column("meta_pages", "instagram_username")
    op.drop_column("meta_pages", "parent_page_id")
    op.drop_column("meta_pages", "record_type")

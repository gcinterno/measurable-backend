"""add wishlist leads

Revision ID: 20260522_000024
Revises: 20260522_000023
Create Date: 2026-05-22 00:00:24

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260522_000024"
down_revision = "20260522_000023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wishlist_leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False, server_default="upgrade_page"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_wishlist_leads_user_id", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_wishlist_leads_workspace_id", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_wishlist_leads_user_id", "wishlist_leads", ["user_id"])
    op.create_index("ix_wishlist_leads_workspace_id", "wishlist_leads", ["workspace_id"])
    op.create_index("ix_wishlist_leads_email", "wishlist_leads", ["email"])
    op.create_index("ix_wishlist_leads_source", "wishlist_leads", ["source"])
    op.create_index("ix_wishlist_leads_created_at", "wishlist_leads", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_wishlist_leads_created_at", table_name="wishlist_leads")
    op.drop_index("ix_wishlist_leads_source", table_name="wishlist_leads")
    op.drop_index("ix_wishlist_leads_email", table_name="wishlist_leads")
    op.drop_index("ix_wishlist_leads_workspace_id", table_name="wishlist_leads")
    op.drop_index("ix_wishlist_leads_user_id", table_name="wishlist_leads")
    op.drop_table("wishlist_leads")
